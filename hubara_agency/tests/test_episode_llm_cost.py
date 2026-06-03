"""Tests del costo LLM por episodio (HU costo-por-venta).

Cubre el cálculo de costo (`compute_llm_cost_usd` + `load_pricing_table`) y la
acumulación **idempotente** al episodio (`_apply_episode_llm_usage`, pura).
"""

from __future__ import annotations

import json
from pathlib import Path

from src.platform.observability.cost_attribution import _apply_episode_llm_usage
from src.platform.observability.pricing import (
    compute_llm_cost_usd,
    load_pricing_table,
)

_TABLE = {
    "deepseek-v4-flash": {"promptPrice": 0.00014, "completionPrice": 0.00028},
    "gemini-backup": {"promptPrice": 0.00025, "completionPrice": 0.0015},
}


# ── pricing ──────────────────────────────────────────────────────────────────


def test_compute_cost_exact_model() -> None:
    # 1000 in × 0.00014/1K + 500 out × 0.00028/1K = 0.00014 + 0.00014 = 0.00028
    assert compute_llm_cost_usd("deepseek-v4-flash", 1000, 500, _TABLE) == 0.00028


def test_compute_cost_provider_prefix_split() -> None:
    # "deepseek/deepseek-v4-flash" → split tras "/" → "deepseek-v4-flash"
    assert compute_llm_cost_usd("deepseek/deepseek-v4-flash", 1000, 0, _TABLE) == 0.00014


def test_compute_cost_unknown_model_zero() -> None:
    assert compute_llm_cost_usd("gpt-inexistente", 1000, 1000, _TABLE) == 0.0


def test_compute_cost_empty_table_zero() -> None:
    assert compute_llm_cost_usd("deepseek-v4-flash", 1000, 1000, {}) == 0.0


def test_load_pricing_table_missing_file() -> None:
    assert load_pricing_table(Path("/no/existe/pricing.json")) == {}


def test_load_pricing_table_from_file(tmp_path: Path) -> None:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps({"chat": _TABLE}), encoding="utf-8")
    assert load_pricing_table(p) == _TABLE


# ── acumulación (pura, idempotente) ──────────────────────────────────────────


def _meta(episode_id: str = "ep_001") -> dict:
    return {"episodes": [{"episode_id": episode_id, "closed_at_ms": None}]}


def test_apply_accumulates_cost_and_tokens() -> None:
    m = _meta()
    applied = _apply_episode_llm_usage(
        m,
        episode_id="ep_001",
        prompt_tokens=1000,
        completion_tokens=500,
        model="deepseek-v4-flash",
        dedup_key="a1",
        pricing_table=_TABLE,
    )
    assert applied is True
    usage = m["episodes"][0]["llm_usage"]
    assert usage["prompt_tokens"] == 1000
    assert usage["completion_tokens"] == 500
    assert usage["total_tokens"] == 1500
    assert usage["cost_usd"] == 0.00028


def test_apply_accumulates_across_turns() -> None:
    m = _meta()
    _apply_episode_llm_usage(
        m, episode_id="ep_001", prompt_tokens=1000, completion_tokens=500,
        model="deepseek-v4-flash", dedup_key="a1", pricing_table=_TABLE,
    )
    _apply_episode_llm_usage(
        m, episode_id="ep_001", prompt_tokens=2000, completion_tokens=0,
        model="deepseek-v4-flash", dedup_key="a2", pricing_table=_TABLE,
    )
    usage = m["episodes"][0]["llm_usage"]
    assert usage["prompt_tokens"] == 3000
    assert usage["completion_tokens"] == 500
    # 0.00028 + (2000/1000 × 0.00014) = 0.00028 + 0.00028 = 0.00056
    assert usage["cost_usd"] == 0.00056


def test_apply_idempotent_same_dedup_key() -> None:
    m = _meta()
    a = _apply_episode_llm_usage(
        m, episode_id="ep_001", prompt_tokens=1000, completion_tokens=500,
        model="deepseek-v4-flash", dedup_key="retry", pricing_table=_TABLE,
    )
    b = _apply_episode_llm_usage(
        m, episode_id="ep_001", prompt_tokens=1000, completion_tokens=500,
        model="deepseek-v4-flash", dedup_key="retry", pricing_table=_TABLE,
    )
    assert a is True and b is False  # 2da vez (retry de Temporal) = no-op
    usage = m["episodes"][0]["llm_usage"]
    assert usage["cost_usd"] == 0.00028  # NO se dobló
    assert usage["prompt_tokens"] == 1000


def test_apply_distinct_runs_same_activity_id_both_count() -> None:
    # Sales (run R1) y Remarketing (run R2) pueden tener el MISMO activity_id ("5")
    # porque el contador se reinicia por workflow run. Con la clave run_id:activity_id
    # NO colisionan → AMBOS turnos cuentan (el bug era contar uno solo).
    m = _meta()
    a = _apply_episode_llm_usage(
        m, episode_id="ep_001", prompt_tokens=1000, completion_tokens=0,
        model="deepseek-v4-flash", dedup_key="R1:5", pricing_table=_TABLE,
    )
    b = _apply_episode_llm_usage(
        m, episode_id="ep_001", prompt_tokens=1000, completion_tokens=0,
        model="deepseek-v4-flash", dedup_key="R2:5", pricing_table=_TABLE,
    )
    assert a is True and b is True
    usage = m["episodes"][0]["llm_usage"]
    assert usage["prompt_tokens"] == 2000  # ambos contaron
    assert usage["cost_usd"] == 0.00028  # 0.00014 × 2


def test_apply_multi_agent_episode_sums_all_turns() -> None:
    # Un episodio = toda la venta: sales (run R1) ×2 + remarketing (run R2) ×2 → 4
    # turnos. R1:3/R2:3 y R1:8/R2:8 = mismo activity_id, distinto run → NO se pisan.
    m = _meta()
    for key in ("R1:3", "R1:8", "R2:3", "R2:8"):
        _apply_episode_llm_usage(
            m, episode_id="ep_001", prompt_tokens=1000, completion_tokens=500,
            model="deepseek-v4-flash", dedup_key=key, pricing_table=_TABLE,
        )
    usage = m["episodes"][0]["llm_usage"]
    assert usage["total_tokens"] == 4 * 1500  # los 4 turnos sumados
    assert usage["cost_usd"] == round(4 * 0.00028, 8)


def test_apply_missing_episode_noop() -> None:
    m = _meta("ep_001")
    applied = _apply_episode_llm_usage(
        m, episode_id="ep_999", prompt_tokens=1000, completion_tokens=500,
        model="deepseek-v4-flash", dedup_key="x", pricing_table=_TABLE,
    )
    assert applied is False
    assert "llm_usage" not in m["episodes"][0]


def test_apply_zero_tokens_noop() -> None:
    m = _meta()
    applied = _apply_episode_llm_usage(
        m, episode_id="ep_001", prompt_tokens=0, completion_tokens=0,
        model="deepseek-v4-flash", dedup_key="x", pricing_table=_TABLE,
    )
    assert applied is False


# ── activity end-to-end (read pricing env + read/write metadata + activity.info) ──


async def test_activity_persists_to_vault(
    _isolate_vault_dir: Path, monkeypatch, tmp_path: Path
) -> None:
    from temporalio.testing import ActivityEnvironment

    from src.platform.observability.cost_attribution import (
        RecordEpisodeLLMUsageInput,
        record_episode_llm_usage_activity,
    )

    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps({"chat": _TABLE}), encoding="utf-8")
    monkeypatch.setenv("OPENLIT_PRICING_JSON", str(pricing))

    session_id = "wa_999"
    sess = _isolate_vault_dir / session_id
    sess.mkdir(parents=True)
    (sess / "metadata.json").write_text(
        json.dumps({"episodes": [{"episode_id": "ep_001", "closed_at_ms": None}]}),
        encoding="utf-8",
    )

    inp = RecordEpisodeLLMUsageInput(
        session_id=session_id,
        episode_id="ep_001",
        prompt_tokens=1000,
        completion_tokens=500,
        model="deepseek-v4-flash",
    )
    await ActivityEnvironment().run(record_episode_llm_usage_activity, inp)

    meta = json.loads((sess / "metadata.json").read_text(encoding="utf-8"))
    usage = meta["episodes"][0]["llm_usage"]
    assert usage["cost_usd"] == 0.00028
    assert usage["total_tokens"] == 1500
