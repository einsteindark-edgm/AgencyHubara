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


# ── costo real: caché y hora pico (2026-10-06) ───────────────────────────────
# DeepSeek cobra el input servido desde su caché a US$0,003/M (vs US$0,15/M) y
# el DOBLE lun-vie 01:00-04:00 y 06:00-10:00 UTC. Antes se cobraba todo a
# precio sin caché de valle: Ads mostraba ~3x el costo real.

from datetime import datetime, timezone  # noqa: E402


def _ms(*args: int) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp() * 1000)


_REAL = {
    "deepseek-v4-flash": {
        "promptPrice": 0.00015,
        "cachedPromptPrice": 0.000003,
        "completionPrice": 0.0006,
        "peak": {"multiplier": 2, "weekdays_utc": [0, 1, 2, 3, 4], "hours_utc": [[1, 4], [6, 10]]},
    }
}
_VALLE = _ms(2026, 10, 6, 12, 0)  # martes 12:00 UTC
_PICO = _ms(2026, 10, 6, 2, 0)  # martes 02:00 UTC = lunes 21:00 Bogotá
_SABADO_02 = _ms(2026, 10, 10, 2, 0)  # sábado: sin pico
_FIN_PICO = _ms(2026, 10, 6, 4, 0)  # 04:00 UTC: ya es valle


def _cost(at_ms: int, **kw: int) -> float:
    return compute_llm_cost_usd("deepseek-v4-flash", 1000, 10, _REAL, at_ms=at_ms, **kw)


def test_cached_tokens_are_charged_at_the_cached_price() -> None:
    # 900 cacheados × 0.000003/1K + 100 sin caché × 0.00015/1K + 10 out × 0.0006/1K
    expected = round(0.9 * 0.000003 + 0.1 * 0.00015 + 0.01 * 0.0006, 8)
    assert _cost(_VALLE, cached_prompt_tokens=900) == expected


def test_peak_hours_double_the_price_only_on_weekdays() -> None:
    valle = _cost(_VALLE, cached_prompt_tokens=900)
    assert _cost(_PICO, cached_prompt_tokens=900) == round(2 * valle, 8)
    assert _cost(_SABADO_02, cached_prompt_tokens=900) == valle
    assert _cost(_FIN_PICO, cached_prompt_tokens=900) == valle


def test_without_a_cached_price_cached_tokens_pay_full_price() -> None:
    """Un modelo sin `cachedPromptPrice` (gemini-backup) no se abarata: la
    cifra nunca queda por debajo de lo real."""
    assert compute_llm_cost_usd("gemini-backup", 1000, 0, _TABLE, cached_prompt_tokens=900) == 0.00025


def test_apply_records_the_cached_tokens_and_the_real_cost() -> None:
    m = _meta()
    _apply_episode_llm_usage(
        m, episode_id="ep_001", prompt_tokens=1000, completion_tokens=10,
        model="deepseek-v4-flash", dedup_key="a1", pricing_table=_REAL,
        cached_prompt_tokens=900, at_ms=_VALLE,
    )
    usage = m["episodes"][0]["llm_usage"]
    assert usage["cached_tokens"] == 900
    assert usage["prompt_tokens"] == 1000
    assert usage["cost_usd"] == _cost(_VALLE, cached_prompt_tokens=900)


def test_the_shipped_pricing_knows_deepseek_cache_and_peak() -> None:
    table = load_pricing_table(Path(__file__).resolve().parents[1] / "deploy/openlit/pricing.json")
    for alias in ("deepseek-v4-flash", "deepseek/deepseek-v4-flash", "deepseek-flash"):
        entry = table[alias]
        assert entry["cachedPromptPrice"] == 0.000003, alias
        assert entry["peak"] == {"multiplier": 2, "weekdays_utc": [0, 1, 2, 3, 4], "hours_utc": [[1, 4], [6, 10]]}, alias


async def test_activity_persists_cached_tokens(
    _isolate_vault_dir: Path, monkeypatch, tmp_path: Path
) -> None:
    from temporalio.testing import ActivityEnvironment

    from src.platform.observability.cost_attribution import (
        RecordEpisodeLLMUsageInput,
        record_episode_llm_usage_activity,
    )

    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps({"chat": _REAL}), encoding="utf-8")
    monkeypatch.setenv("OPENLIT_PRICING_JSON", str(pricing))
    sess = _isolate_vault_dir / "wa_998"
    sess.mkdir(parents=True)
    (sess / "metadata.json").write_text(
        json.dumps({"episodes": [{"episode_id": "ep_001", "closed_at_ms": None}]}), encoding="utf-8"
    )

    await ActivityEnvironment().run(
        record_episode_llm_usage_activity,
        RecordEpisodeLLMUsageInput(
            session_id="wa_998", episode_id="ep_001", prompt_tokens=1000,
            completion_tokens=10, model="deepseek-v4-flash", cached_prompt_tokens=900,
        ),
    )

    usage = json.loads((sess / "metadata.json").read_text(encoding="utf-8"))["episodes"][0]["llm_usage"]
    assert usage["cached_tokens"] == 900
    assert usage["cost_usd"] in (_cost(_VALLE, cached_prompt_tokens=900), _cost(_PICO, cached_prompt_tokens=900))
