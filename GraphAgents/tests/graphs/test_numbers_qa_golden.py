"""Golden-replay de `numbers-qa`: pasa cuando las métricas reconcilian; FALLA cuando un
número fue editado a mano (reconciliación byte-for-byte = el no-self-review)."""
from __future__ import annotations

from graphs.numbers_qa import run
from tools.blended_unit_economics.impl import run as blended_unit_economics

TOOLS = {"blended-unit-economics": blended_unit_economics}
_RAW = {"spend_cop": 300000, "inline_link_clicks": 500, "conversations_started": 100,
        "total_orders": 15, "total_revenue_cop": 450000}


def _day(metrics_override: dict | None = None) -> dict:
    metrics = metrics_override or blended_unit_economics(payload=_RAW)
    return {"date": "2026-06-15", **_RAW, "metrics": metrics,
            "diagnosis": {"high_friction": True, "poor_profitability": True, "recommendation": "rotate_creative"}}


def test_pasa_cuando_reconcilia() -> None:
    out = run({"days": [_day()]}, tools=TOOLS)
    assert out == {"passed": True, "violations": []}


def test_falla_cuando_un_numero_fue_editado_a_mano() -> None:
    # drop_off computado es "0.8"; lo adulteramos a "0.5" → no reconcilia.
    tampered = _day({"drop_off_rate": "0.5", "cost_per_conversation_cop": "3000", "mer": "1.5",
                     "global_cpa_cop": "20000", "global_win_rate": "0.15"})
    out = run({"days": [tampered]}, tools=TOOLS)
    assert out["passed"] is False
    assert any("reconcilian" in v["issue"] for v in out["violations"])


def test_falla_cuando_el_periodo_fue_editado() -> None:
    # MF-2: el verdict de cabecera sale del PERIODO → el QA debe reconciliarlo también.
    good = blended_unit_economics(payload=_RAW)
    tampered_period = {**_RAW, "metrics": {**good, "mer": "999"}}  # MER del periodo adulterado
    out = run({"days": [], "period": tampered_period}, tools=TOOLS)
    assert out["passed"] is False
    assert any(v["date"] == "TOTAL" for v in out["violations"])
