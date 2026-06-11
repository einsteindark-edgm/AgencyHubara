"""Tests de unidad del harness de evals — NO requieren juez LLM ni el extra deepeval.

Cubren la lógica determinista y pura: redacción de PII, reconstrucción de
conversaciones desde el JSONL, selección/muestreo, la rúbrica del guion, las
métricas DETERMINISTAS (saludo + estilo) y la auto-curación (parte pura). Corren
en la suite default (`uv run pytest`), sin tocar SigNoz, Temporal ni litellm.

Las métricas deterministas usan un test case duck-typed (no se necesita
`deepeval.test_case.ConversationalTestCase`): solo leen `.turns` y `.role/.content`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.plugins.chats.agent.sales_eval.evals import script_rubric as R
from src.plugins.chats.agent.sales_eval.evals import (
    curation,
    metrics,
    reconstruct,
    redaction,
    select,
)
from src.plugins.chats.agent.sales_eval.evals.contracts import EvalWindowInput

_GOLDENS = Path(__file__).parent / "goldens" / "sales" / "curated.json"


# --------------------------------------------------------------------------- #
# Fakes (duck-typed) para las métricas deterministas.
# --------------------------------------------------------------------------- #
class _Turn:
    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


class _TC:
    def __init__(self, turns: list[_Turn]) -> None:
        self.turns = turns


def _tc_from_pairs(pairs: list[tuple[str, str]]) -> _TC:
    return _TC([_Turn(r, c) for r, c in pairs])


# --------------------------------------------------------------------------- #
# Redacción de PII.
# --------------------------------------------------------------------------- #
def test_redact_phone_and_email():
    out = redaction.redact_pii("escríbeme a juan@correo.com o al +57 300 111 2233")
    assert "<EMAIL>" in out
    assert "<PHONE>" in out
    assert "juan@correo.com" not in out
    assert "2233" not in out


def test_redact_is_idempotent():
    once = redaction.redact_pii("mi cel es 3001112233")
    twice = redaction.redact_pii(once)
    assert once == twice
    assert "<" in once  # algo se redactó


def test_redact_preserves_short_numbers_and_prices():
    # Precios y números cortos NO son PII.
    out = redaction.redact_pii("la vela cuesta $45.000 y mide 90 cm")
    assert "45.000" in out
    assert "90" in out


# --------------------------------------------------------------------------- #
# Reconstrucción desde el JSONL del vault.
# --------------------------------------------------------------------------- #
def _write_session(vault: Path, session_id: str, events: list[dict]) -> None:
    d = vault / session_id / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    with (d / f"{session_id}.jsonl").open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def test_reconstruct_reads_and_normalizes(tmp_path: Path):
    sid = "wa_+573001112233"
    _write_session(tmp_path, sid, [
        {"role": "user", "content": "hola, mi numero es 3001112233"},
        {"role": "assistant", "content": "Buenas tardes. Bienvenido a Hubara.",
         "tool_calls": [{"name": "send_quick_replies"}]},
        {"role": "assistant", "sender": "human", "content": "hola soy un humano"},
        {"role": "assistant", "content": "esto NO debería aparecer (post-takeover)"},
    ])
    events = reconstruct.read_session_events(tmp_path, sid)
    assert len(events) == 4

    turns = reconstruct.to_evaluable_turns(events, redact=True, stop_at_human_takeover=True)
    # Corta en el takeover humano → solo user + 1 assistant del bot.
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert "<PHONE>" in turns[0]["content"]  # redactado
    assert turns[1]["tools"] == ["send_quick_replies"]


def test_whatsapp_number_from_session():
    assert reconstruct.whatsapp_number_from_session("wa_+573001112233") == "+573001112233"
    assert reconstruct.whatsapp_number_from_session("sinprefijo") == "sinprefijo"


def test_reconstruct_missing_jsonl_is_empty(tmp_path: Path):
    assert reconstruct.read_session_events(tmp_path, "wa_inexistente") == []


# --------------------------------------------------------------------------- #
# Episodios: unit ids + slicing del JSONL por episodio.
# --------------------------------------------------------------------------- #
def test_eval_unit_id_roundtrip():
    assert reconstruct.make_eval_unit_id("wa_x", "ep_002") == "wa_x::ep_002"
    assert reconstruct.parse_eval_unit_id("wa_x::ep_002") == ("wa_x", "ep_002")
    # Legacy / sesión entera: sin episodio.
    assert reconstruct.make_eval_unit_id("wa_x") == "wa_x"
    assert reconstruct.parse_eval_unit_id("wa_x") == ("wa_x", "")


def test_slice_episode_by_msgs_counts():
    events = [{"role": "user", "content": f"m{i}"} for i in range(10)]
    ep = {"episode_id": "ep_001", "msgs_count_at_start": 3, "msgs_count_at_close": 7}
    assert reconstruct.slice_episode_events(events, ep) == events[3:7]
    # Episodio activo (sin close): hasta el final.
    ep_activo = {"episode_id": "ep_002", "msgs_count_at_start": 7,
                 "msgs_count_at_close": None}
    assert reconstruct.slice_episode_events(events, ep_activo) == events[7:]


def test_slice_episode_by_timestamps_fallback():
    def ev(ts_iso: str, i: int) -> dict:
        return {"role": "user", "content": f"m{i}", "timestamp": ts_iso}

    events = [
        ev("2026-06-01T10:00:00+00:00", 0),   # antes del episodio
        ev("2026-06-02T10:00:00+00:00", 1),   # dentro
        ev("2026-06-02T11:00:00+00:00", 2),   # dentro
        ev("2026-06-03T10:00:00+00:00", 3),   # después del cierre
        {"role": "user", "content": "sin ts"},  # sin timestamp → fuera en modo ts
    ]
    import datetime as dt

    start = int(dt.datetime.fromisoformat("2026-06-02T00:00:00+00:00").timestamp() * 1000)
    close = int(dt.datetime.fromisoformat("2026-06-03T00:00:00+00:00").timestamp() * 1000)
    ep = {"episode_id": "ep_001", "started_at_ms": start, "closed_at_ms": close}
    sliced = reconstruct.slice_episode_events(events, ep)
    assert [e["content"] for e in sliced] == ["m1", "m2"]


def test_slice_episode_without_bounds_returns_all():
    events = [{"role": "user", "content": "a"}]
    assert reconstruct.slice_episode_events(events, {"episode_id": "ep_001"}) == events


def test_read_episode_events_full_session_when_episode_missing(tmp_path: Path):
    sid = "wa_x"
    _write_session(tmp_path, sid, [{"role": "user", "content": "hola"}])
    # Sin metadata / episodio inexistente → sesión entera, episode None.
    events, ep = reconstruct.read_episode_events(tmp_path, sid, "ep_999")
    assert len(events) == 1 and ep is None
    events, ep = reconstruct.read_episode_events(tmp_path, sid, "")
    assert len(events) == 1 and ep is None


# --------------------------------------------------------------------------- #
# Selección / muestreo.
# --------------------------------------------------------------------------- #
def test_select_respects_min_turns_window_and_handoff(tmp_path: Path):
    # Larga (5 turnos) — debe entrar.
    _write_session(tmp_path, "wa_larga", [{"role": "user", "content": f"m{i}"} for i in range(5)])
    # Corta (2 turnos) — bajo min_turns → fuera.
    _write_session(tmp_path, "wa_corta", [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}])
    # Handoff (4 turnos + metadata humano) — debe entrar y priorizarse.
    _write_session(tmp_path, "wa_handoff", [{"role": "user", "content": f"m{i}"} for i in range(4)])
    (tmp_path / "wa_handoff" / "metadata.json").write_text(
        json.dumps({"active_route": "humano", "tag": "HUMANO"}), encoding="utf-8"
    )

    window = EvalWindowInput(lookback_hours=24, max_conversations=10, min_turns=4)
    selected = select.select_sessions(window, vault_dir=tmp_path)

    assert "wa_corta" not in selected           # filtrada por min_turns
    assert "wa_larga" in selected
    assert "wa_handoff" in selected
    assert selected[0] == "wa_handoff"          # handoff priorizado primero


def test_select_excludes_out_of_window(tmp_path: Path):
    import os
    import time

    _write_session(tmp_path, "wa_vieja", [{"role": "user", "content": f"m{i}"} for i in range(5)])
    old = time.time() - 100 * 3600  # 100h atrás
    os.utime(tmp_path / "wa_vieja" / "sessions" / "wa_vieja.jsonl", (old, old))

    window = EvalWindowInput(lookback_hours=8, max_conversations=10, min_turns=4)
    selected = select.select_sessions(window, vault_dir=tmp_path, now=time.time())
    assert "wa_vieja" not in selected


# --------------------------------------------------------------------------- #
# Selección por EPISODIO (select_eval_units).
# --------------------------------------------------------------------------- #
def _write_metadata(vault: Path, session_id: str, metadata: dict) -> None:
    (vault / session_id).mkdir(parents=True, exist_ok=True)
    (vault / session_id / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
    )


def test_select_units_expands_episodes_and_applies_min_turns(tmp_path: Path):
    import time

    now_ms = int(time.time() * 1000)
    # 10 mensajes: ep_001 (cerrado hace 1h) ocupa [0:6) = 6 turnos; ep_002
    # (activo) ocupa [6:10) = 4 turnos. Con min_turns=5, solo ep_001 califica —
    # min_turns se aplica POR EPISODIO, no por el total de la sesión (10).
    _write_session(tmp_path, "wa_multi", [{"role": "user", "content": f"m{i}"} for i in range(10)])
    _write_metadata(tmp_path, "wa_multi", {
        "episodes": [
            {"episode_id": "ep_001", "started_at_ms": now_ms - 7200_000,
             "closed_at_ms": now_ms - 3600_000,
             "msgs_count_at_start": 0, "msgs_count_at_close": 6},
            {"episode_id": "ep_002", "started_at_ms": now_ms - 1800_000,
             "closed_at_ms": None,
             "msgs_count_at_start": 6, "msgs_count_at_close": None},
        ],
    })
    window = EvalWindowInput(lookback_hours=24, max_conversations=10, min_turns=5)
    units = select.select_eval_units(window, vault_dir=tmp_path, now=time.time())
    assert units == ["wa_multi::ep_001"]  # ep_002 (4 turnos) < min_turns=5


def test_select_units_includes_active_and_recent_closed_excludes_old_closed(tmp_path: Path):
    import time

    now = time.time()
    now_ms = int(now * 1000)
    _write_session(tmp_path, "wa_eps", [{"role": "user", "content": f"m{i}"} for i in range(12)])
    _write_metadata(tmp_path, "wa_eps", {
        "episodes": [
            # Cerrado hace 3 días → fuera de la ventana de 24h.
            {"episode_id": "ep_001", "started_at_ms": now_ms - 80 * 3600_000,
             "closed_at_ms": now_ms - 72 * 3600_000,
             "msgs_count_at_start": 0, "msgs_count_at_close": 4},
            # Cerrado hace 1h → dentro.
            {"episode_id": "ep_002", "started_at_ms": now_ms - 7200_000,
             "closed_at_ms": now_ms - 3600_000,
             "msgs_count_at_start": 4, "msgs_count_at_close": 8},
            # Activo → dentro.
            {"episode_id": "ep_003", "started_at_ms": now_ms - 600_000,
             "closed_at_ms": None,
             "msgs_count_at_start": 8, "msgs_count_at_close": None},
        ],
    })
    window = EvalWindowInput(lookback_hours=24, max_conversations=10, min_turns=4)
    units = select.select_eval_units(window, vault_dir=tmp_path, now=now)
    assert "wa_eps::ep_001" not in units
    assert set(units) == {"wa_eps::ep_002", "wa_eps::ep_003"}


def test_select_units_legacy_session_without_episodes(tmp_path: Path):
    import time

    _write_session(tmp_path, "wa_legacy", [{"role": "user", "content": f"m{i}"} for i in range(5)])
    window = EvalWindowInput(lookback_hours=24, max_conversations=10, min_turns=4)
    units = select.select_eval_units(window, vault_dir=tmp_path, now=time.time())
    assert units == ["wa_legacy"]  # unit id pelado = sesión entera


# --------------------------------------------------------------------------- #
# Rúbrica del guion (regex/listas).
# --------------------------------------------------------------------------- #
def test_rubric_greeting_and_brand():
    assert R.GREETING_RE.search("Buenas tardes a todos")
    assert R.GREETING_RE.search("buenos dias")
    assert not R.GREETING_RE.search("hey que tal")
    assert R.BRAND_RE.search("Bienvenido a Hubara")


def test_rubric_forbidden_openers_and_voseo():
    assert any(p.search("¡Hola! como estas") for p in R.FORBIDDEN_OPENERS)
    assert any(p.search("tenés que ver esto") for p in R.VOSEO_RES)
    assert any(p.search("dale, joya") for p in R.VOSEO_RES)
    assert not any(p.search("tienes que ver esto") for p in R.VOSEO_RES)  # tuteo OK


def test_rubric_dash_emoji_and_forbidden_closings():
    assert R.DASH_RE.search("texto — con em dash")
    assert R.disallowed_emojis("hola 🔥") == ["🔥"]
    assert R.disallowed_emojis("gracias 🤍") == []  # allowlist
    assert any(p.search("gracias por tu compra") for p in R.FORBIDDEN_CLOSINGS)
    assert any(p.search("la conversación queda cerrada") for p in R.FORBIDDEN_CLOSINGS)


# --------------------------------------------------------------------------- #
# Métricas DETERMINISTAS (sin juez).
# --------------------------------------------------------------------------- #
def test_greeting_metric_pass_and_fail():
    g = metrics.GreetingComplianceMetric()
    ok = _tc_from_pairs([("user", "hola"),
                         ("assistant", "Buenas tardes. Bienvenido a Hubara, velas de cera de palma.")])
    assert g.measure(ok) == 1.0 and g.is_successful()

    bad = metrics.GreetingComplianceMetric()
    assert bad.measure(_tc_from_pairs([("assistant", "¡Hola! que necesitas")])) == 0.0
    assert not bad.is_successful()


def test_style_metric_detects_voseo_dash_emoji():
    s = metrics.StyleComplianceMetric()
    bad = _tc_from_pairs([("assistant", "tenés que verlo — está buenísimo 🔥🔥")])
    score = s.measure(bad)
    assert score < 1.0 and not s.is_successful()
    assert "voseo" in s.reason or "em dash" in s.reason

    good = metrics.StyleComplianceMetric()
    clean = _tc_from_pairs([("assistant", "Tienes que verla, te encantará 🤍")])
    assert good.measure(clean) == 1.0 and good.is_successful()


def test_style_metric_flags_forbidden_closing():
    s = metrics.StyleComplianceMetric()
    bad = _tc_from_pairs([("assistant", "Gracias por tu compra, la conversación queda cerrada")])
    assert s.measure(bad) < 1.0


def test_metric_key_is_stable_snake_case():
    assert metrics.metric_key(metrics.GreetingComplianceMetric()) == "greeting_compliance"
    assert metrics.metric_key(metrics.StyleComplianceMetric()) == "style_compliance"


# --------------------------------------------------------------------------- #
# Los goldens seed PASAN las métricas deterministas (regresión).
# --------------------------------------------------------------------------- #
def test_seed_goldens_pass_deterministic_metrics():
    goldens = json.loads(_GOLDENS.read_text(encoding="utf-8"))
    assert len(goldens) >= 5
    for g in goldens:
        tc = _TC([_Turn(t["role"], t["content"]) for t in g["turns"]])
        greeting = metrics.GreetingComplianceMetric()
        style = metrics.StyleComplianceMetric()
        assert greeting.measure(tc) == 1.0, f"{g['name']}: {greeting.reason}"
        assert style.measure(tc) == 1.0, f"{g['name']}: {style.reason}"


# --------------------------------------------------------------------------- #
# Auto-curación (parte pura).
# --------------------------------------------------------------------------- #
def test_build_golden_draft_prompt_contains_transcript_and_fails():
    turns = [{"role": "user", "content": "hola"}, {"role": "assistant", "content": "hey"}]
    failed = [("greeting_compliance", 0.0, "no saludó")]
    prompt = curation.build_golden_draft_prompt(turns, failed)
    assert "greeting_compliance" in prompt
    assert "Cliente: hola" in prompt and "Asesor: hey" in prompt
    assert "RESULTADO ESPERADO" in prompt


def test_build_and_write_candidate(tmp_path: Path):
    turns = [{"role": "user", "content": "hola"}, {"role": "assistant", "content": "hey"}]
    scores = [("greeting_compliance", 0.0, False, "no saludó"),
              ("style_compliance", 1.0, True, "ok")]
    golden = curation.build_candidate_golden(
        session_id="wa_+57X", turns=turns, scenario="esc",
        expected_outcome="debió saludar", scores=scores,
    )
    assert golden["expected_outcome"] == "debió saludar"
    assert golden["turns"] == turns
    assert golden["additional_metadata"]["status"] == "needs_human_review"
    assert len(golden["additional_metadata"]["failed_metrics"]) == 1

    path = curation.write_candidate(tmp_path, "wa_+57X", golden)
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["expected_outcome"] == "debió saludar"


def test_candidate_carries_episode_and_filename_is_per_unit(tmp_path: Path):
    turns = [{"role": "user", "content": "hola"}, {"role": "assistant", "content": "hey"}]
    scores = [("greeting_compliance", 0.0, False, "no saludó")]
    golden = curation.build_candidate_golden(
        session_id="wa_+57X", turns=turns, scenario="esc",
        expected_outcome="debió saludar", scores=scores, episode_id="ep_002",
    )
    assert golden["additional_metadata"]["source_episode"] == "ep_002"
    assert golden["additional_metadata"]["source_session_redacted"] == "wa_+57X"

    # El filename incluye el episodio: re-eval del MISMO episodio pisa, episodios
    # distintos de la misma sesión conviven.
    p1 = curation.write_candidate(tmp_path, "wa_+57X::ep_001", golden)
    p2 = curation.write_candidate(tmp_path, "wa_+57X::ep_002", golden)
    assert p1 != p2
    assert p1.name == "wa_+57X__ep_001.json"
    assert {p.name for p in tmp_path.glob("*.json")} == {
        "wa_+57X__ep_001.json", "wa_+57X__ep_002.json",
    }


# --------------------------------------------------------------------------- #
# Contracts.
# --------------------------------------------------------------------------- #
def test_eval_window_defaults():
    w = EvalWindowInput()
    assert w.lookback_hours == 8
    assert w.max_conversations == 50
    assert w.draft_goldens is True
    assert w.redact_pii is True


@pytest.mark.parametrize("score,threshold,verdict", [(0.9, 0.7, True), (0.5, 0.7, False)])
def test_window_candidate_threshold_semantics(score, threshold, verdict):
    # avg_score < candidate_threshold ⇒ candidata. (Documenta la semántica.)
    is_candidate = score < threshold
    assert is_candidate is (not verdict)
