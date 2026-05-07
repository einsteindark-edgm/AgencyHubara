"""Tests del helper compartido `run_agent_turn` (Fase 5).

El helper se ejecuta dentro del `@workflow.run` de los workflows productivos. Aqui
solo testeamos las piezas puras:
  * `_try_parse_decision_payload` parsea JSON con `transfer_decision`.
  * `_try_parse_decision_payload` retorna None ante texto plano o JSON invalido.
  * Las dataclasses `PendingMessage` / `TurnResult` son JSON-serializables (R-JSON).
"""
from __future__ import annotations

import json
from dataclasses import asdict

from src.platform.workflow_helpers import (
    PendingMessage,
    TurnResult,
    _try_parse_decision_payload,
)
from src.platform.contracts import ScheduleRemarketingDecision, TransferDecision


def test_parse_decision_with_transfer() -> None:
    raw = json.dumps({
        "transfer_decision": {
            "session_id": "wa_X",
            "target_route": "ventas",
            "summary": "interesado",
        },
        "message": "ok",
    })
    parsed = _try_parse_decision_payload(raw)
    assert parsed is not None
    assert parsed["transfer_decision"]["session_id"] == "wa_X"


def test_parse_decision_with_schedule_remarketing() -> None:
    raw = json.dumps({
        "schedule_remarketing": {
            "session_id": "wa_Y",
            "motivo": "duda precio",
            "delay_seconds": 60,
        },
        "message": "ok",
    })
    parsed = _try_parse_decision_payload(raw)
    assert parsed is not None
    assert parsed["schedule_remarketing"]["delay_seconds"] == 60


def test_parse_decision_returns_none_on_plain_text() -> None:
    assert _try_parse_decision_payload("not a json") is None


def test_parse_decision_returns_none_on_non_dict_json() -> None:
    assert _try_parse_decision_payload("[1, 2, 3]") is None


def test_pending_message_is_dataclass_serializable() -> None:
    pm = PendingMessage(message="hola", media=None, plugin_context=["ctx"])
    payload = asdict(pm)
    assert payload == {"message": "hola", "media": None, "plugin_context": ["ctx"]}


def test_turn_result_default_serializable() -> None:
    tr = TurnResult(final_content="hi", tools_used=["foo"])
    payload = asdict(tr)
    assert payload["final_content"] == "hi"
    assert payload["tools_used"] == ["foo"]
    assert payload["transfer_decision"] is None
    assert payload["schedule_remarketing"] is None


def test_turn_result_with_decisions_serializable() -> None:
    tr = TurnResult(
        final_content="ok",
        tools_used=["transfer_to_sales_agent"],
        transfer_decision=TransferDecision(session_id="wa_A", target_route="ventas", summary="x"),
        schedule_remarketing=ScheduleRemarketingDecision(
            session_id="wa_B", motivo="m", delay_seconds=60
        ),
    )
    payload = asdict(tr)
    assert payload["transfer_decision"]["session_id"] == "wa_A"
    assert payload["schedule_remarketing"]["motivo"] == "m"
