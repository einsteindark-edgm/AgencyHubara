"""Golden-replay de `window-strategist`: dado el snapshot fixture, el grafo
produce EXACTAMENTE esta lista de dispatch/suppressed/truncated.

Cubre el guardrail autónomo del nodo `plan` (el reemplazo del approval humano):
  * cadencia por lead (max_touches_per_24h) → `cadence_cap`,
  * tope duro de toques PAGOS por ciclo → `paid_budget_truncated` + contador
    `truncated_by_budget` VISIBLE (no silent cap),
  * orden por valor esperado: transactional_hook > ctwa_72h_free >
    csw_free_form > phase_b_high_value.

Sin HITL: el output del run() ES la lista de intents (G-DUR sin sujeto — cero
tools outward). `now` viaja en el payload (G-DET, L-1).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from graphs.window_strategist import run
from tools.parse_conversations.impl import run as parse_conversations

GA = Path(__file__).resolve().parents[2]
FIX = GA / "fixtures" / "window_strategist_snapshot.json"

TOOLS = {"parse-conversations": parse_conversations}


def _payload() -> dict:
    return json.loads(FIX.read_text(encoding="utf-8"))


def test_golden_dispatch_list_exacta() -> None:
    out = run({"payload": _payload()}, tools=TOOLS)
    assert out == {
        "schema_version": 1,
        "snapshot_now_ms": 1716700000000,
        "dispatch": [
            {
                "kind": "reengagement_dispatch",
                "session_id": "wa_phase_b_hook",
                "recommended_channel": "template",
                "recommended_category": "utility",
                "reason": "transactional_hook",
                "priority": 1,
            },
            {
                "kind": "reengagement_dispatch",
                "session_id": "wa_ctwa_hook",
                "recommended_channel": "template",
                "recommended_category": "utility",
                "reason": "ctwa_72h_free",
                "priority": 2,
            },
            {
                "kind": "reengagement_dispatch",
                "session_id": "wa_csw_active",
                "recommended_channel": "free_form",
                "recommended_category": "service",
                "reason": "csw_free_form",
                "priority": 3,
            },
        ],
        "suppressed": [
            {"session_id": "wa_cold", "reason": "fase_b_cold_suppressed"},
            {"session_id": "wa_cadence_capped", "reason": "cadence_cap"},
            {"session_id": "wa_human", "reason": "human_owned"},
            {"session_id": "wa_phase_b_optin", "reason": "paid_budget_truncated"},
        ],
        "truncated_by_budget": 1,
    }


def test_falta_payload_da_error_de_dominio() -> None:
    with pytest.raises(ValueError):  # MF-7: no KeyError crudo
        run({}, tools=TOOLS)


def test_snapshot_vacio_emite_lista_vacia() -> None:
    out = run(
        {"payload": {"now_ms": 1716700000000, "conversations": []}}, tools=TOOLS
    )
    assert out["dispatch"] == []
    assert out["suppressed"] == []
    assert out["truncated_by_budget"] == 0
