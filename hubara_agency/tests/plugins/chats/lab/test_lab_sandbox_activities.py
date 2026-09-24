"""Activities del turno simulado (plan §3.3 y §3.6, PR 11).

El sandbox usa la MISMA lista de activities del worker de ventas
(`workers/sales.py::SALES_ACTIVITIES`) y cambia solo las que tienen efectos
fuera del sandbox o que no aplican a un turno aislado. Una activity nueva en
producción que el sandbox no clasifique hace fallar la corrida: nunca corre
sin que alguien decida qué hace en el laboratorio.
"""
from __future__ import annotations

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError

from src.plugins.chats.agent.sales_lab.sandbox.activities import (
    FAKED_IN_SANDBOX,
    REAL_IN_SANDBOX,
    SandboxCapture,
    UnclassifiedActivityError,
    sandbox_activities,
)


def _named(name: str, calls: list | None = None):
    @activity.defn(name=name)
    async def _act(*args):
        if calls is not None:
            calls.append((name, args))
        return True

    return _act


def _names(acts) -> list[str]:
    return [a.__temporal_activity_definition.name for a in acts]


def test_real_and_faked_sets_do_not_overlap() -> None:
    assert not REAL_IN_SANDBOX & set(FAKED_IN_SANDBOX)


def test_an_unclassified_production_activity_stops_the_sandbox() -> None:
    with pytest.raises(UnclassifiedActivityError, match="nueva_activity"):
        sandbox_activities([_named("llm_chat"), _named("nueva_activity")], capture=SandboxCapture())


def test_real_activities_pass_through_and_effectful_ones_are_replaced() -> None:
    real = [_named(n) for n in ("llm_chat", "execute_tool", "orchestration.dispatch_event", "read_idle_timeout_seconds")]

    acts = sandbox_activities(real, capture=SandboxCapture())

    assert _names(acts) == ["llm_chat", "execute_tool", "orchestration.dispatch_event", "read_idle_timeout_seconds"]
    assert acts[0] is real[0]
    # `execute_tool` corre la tool real, envuelto: las que leen el pedido en
    # vivo devuelven lo grabado en el turno real (test_lab_sandbox_order_replay).
    assert acts[2] is not real[2] and acts[3] is not real[3]


def test_llm_override_replaces_llm_chat_for_tests() -> None:
    fake_llm = _named("llm_chat")

    acts = sandbox_activities([_named("llm_chat")], capture=SandboxCapture(), llm_chat=fake_llm)

    assert acts == [fake_llm]


async def test_fakes_capture_instead_of_acting() -> None:
    capture = SandboxCapture()
    acts = {a.__temporal_activity_definition.name: a for a in sandbox_activities(
        [_named(n) for n in FAKED_IN_SANDBOX], capture=capture)}

    assert await acts["read_idle_timeout_seconds"]("wa_573000000099") >= 3600
    assert await acts["send_typing_indicator_activity"]("wa_573000000099") is None
    result = await acts["orchestration.dispatch_event"]({"event_type": "EpisodeClosedEvent", "source_plugin": "chats",
                                                          "source_worker": "sales", "payload": {}})
    assert result["matches"] == [] and result["event_type"] == "EpisodeClosedEvent"
    assert await acts["start_or_signal_sales_workflow"]({"session_id": "x"}) is None
    assert await acts["schedule_remarketing_workflow"]({"session_id": "x"}) is None
    assert await acts["write_pending_handoff"]("x", "resumen") is None
    assert [c["activity"] for c in capture.effects] == [
        "orchestration.dispatch_event", "start_or_signal_sales_workflow", "schedule_remarketing_workflow", "write_pending_handoff",
    ]
    with pytest.raises(ApplicationError, match="ghosting"):
        await acts["decide_ghosting_action"]()
    with pytest.raises(ApplicationError, match="audio"):
        await acts["transcribe_audio_activity"]("x")


async def test_the_trace_is_persisted_for_real_and_announces_the_end_of_the_turn() -> None:
    calls: list = []
    capture = SandboxCapture()
    [trace] = sandbox_activities([_named("persist_turn_trace", calls)], capture=capture)

    assert not capture.turn_done.is_set()
    assert await trace("wa_573000000099", '{"turn_started_ms": 1}') is True

    assert calls == [("persist_turn_trace", ("wa_573000000099", '{"turn_started_ms": 1}'))]
    assert capture.turn_done.is_set()
    assert capture.trace_payload == {"turn_started_ms": 1}


def test_the_classifier_layers_run_for_real_in_the_sandbox() -> None:
    """Los bots nuevos (B y C, PR 15) llaman al clasificador de verdad: en la
    caja, por OpenRouter con la llave del laboratorio; en CI, con el
    proveedor falso (`PERCEPTION_PROVIDER=fake`)."""
    assert {"perceive_burst", "verify_coverage"} <= REAL_IN_SANDBOX


async def test_a_scheduled_complement_keeps_the_case_open_until_its_own_trace() -> None:
    """Con el bot nuevo, la verificación puede agendar UN complemento: es un
    segundo turno (de sistema) que forma parte de la respuesta del caso. La
    traza del cliente lo avisa (`complement_scheduled`) y el sandbox espera
    esa segunda traza en vez de adivinar con un tiempo."""
    import json

    capture = SandboxCapture()
    [trace] = sandbox_activities([_named("persist_turn_trace")], capture=capture)
    customer = {"trigger": "customer", "steps": [{"kind": "verify", "decision": "complement", "complement_scheduled": True}]}

    await trace("wa_573000000099", json.dumps(customer))
    assert not capture.turn_done.is_set()

    await trace("wa_573000000099", json.dumps({"trigger": "complement", "steps": []}))
    assert capture.turn_done.is_set()
    assert [t["trigger"] for t in capture.trace_payloads] == ["customer", "complement"]
