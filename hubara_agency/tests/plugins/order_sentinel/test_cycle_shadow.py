"""Los desacuerdos LLM ↔ Jev del Order Sentinel van a la cola que califica
Claude Code (motor de decisiones, F8).

Con el lector de Jev en sombra o en jev, el agente devuelve `shadow`: lo que
el LLM y Jev harían despachar en cada conversación. El workflow lleva SOLO los
desacuerdos a la cola (una para todo el sistema), después de ejecutar los
intents. Sin lector el result no trae `shadow` y el ciclo es el de hoy (las
historias viejas no cambian: la rama depende del resultado grabado). Si
registrar falla, el ciclo igual termina: es observabilidad, no la venta.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import Worker

from src.plugins.order_sentinel.agent.cycle.workflows.cycle import OrderSentinelCycleWorkflow
from src.sdk.connectorkit import DisagreementLog
from tests.plugins.order_sentinel.test_cycle_workflow import _RESULT, QUEUE, Tracker, _fakes

_SHADOW = [
    {
        "session_id": "wa_a",
        "order_id": "order_A",
        "llm": {"action": "none"},
        "jev": {"action": "transition", "to_stage": "shipping"},
        "agree": False,
    },
    {
        "session_id": "wa_c",
        "order_id": "order_C",
        "llm": {"action": "confirm_payment"},
        "jev": {"action": "confirm_payment"},
        "agree": True,
    },
]


async def _run(tracker: Tracker, *, shadow: list | None, record_fails: bool = False) -> tuple[dict, list]:
    recorded: list = []

    @activity.defn(name="record_order_sentinel_shadow")
    async def fake_record(shadow_rows: list, conversations: list) -> dict:
        if record_fails:
            raise ApplicationError("disco lleno", non_retryable=True)
        recorded.append((shadow_rows, conversations))
        return {"compared": len(shadow_rows), "disagreements": 1}

    result = dict(_RESULT)
    if shadow is not None:
        result.update(reader="sombra", shadow=shadow)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[OrderSentinelCycleWorkflow],
            activities=[*_fakes(tracker, poll_result=result), fake_record],
        ):
            summary = await env.client.execute_workflow(
                OrderSentinelCycleWorkflow.run, id="order-sentinel-shadow-test", task_queue=QUEUE
            )
    return summary, recorded


@pytest.mark.asyncio
async def test_los_desacuerdos_de_la_sombra_van_a_la_cola_despues_de_ejecutar():
    tracker = Tracker()
    summary, recorded = await _run(tracker, shadow=_SHADOW)

    assert tracker.executed is not None, "los intents se ejecutan igual que hoy"
    [(rows, conversations)] = recorded
    assert rows == _SHADOW
    assert conversations == [{"session_id": "wa_a", "order_id": "order_A"}], "las conversaciones del snapshot"
    assert summary["reader"] == "sombra"
    assert (summary["shadow_compared"], summary["shadow_disagreements"]) == (2, 1)
    assert summary["run_status"] == "completed"


@pytest.mark.asyncio
async def test_si_el_llm_y_jev_coinciden_no_se_registra_nada():
    tracker = Tracker()
    summary, recorded = await _run(tracker, shadow=[_SHADOW[1]])

    assert recorded == []
    assert (summary["shadow_compared"], summary["shadow_disagreements"]) == (1, 0)


@pytest.mark.asyncio
async def test_sin_lector_el_resumen_es_el_de_hoy():
    tracker = Tracker()
    summary, recorded = await _run(tracker, shadow=None)

    assert recorded == []
    assert "reader" not in summary
    assert "shadow_compared" not in summary


@pytest.mark.asyncio
async def test_si_registrar_falla_el_ciclo_igual_termina():
    tracker = Tracker()
    summary, _ = await _run(tracker, shadow=_SHADOW, record_fails=True)

    assert summary["run_status"] == "completed"
    assert summary["applied"] == 1
    assert summary["shadow_disagreements"] == 0


def _conversation() -> dict:
    return {
        "session_id": "wa_573007654321",
        "order_id": "order_01SALIO",
        "current_stage": "ready",
        "payment_confirmed": True,
        "messages": [
            {"who": "customer", "text": "soy Ana María, ¿ya salió? mi cel 3007654321", "at_ms": 1, "has_media": False},
            {"who": "human_operator", "text": "ya salió con el mensajero", "at_ms": 2, "has_media": False},
        ],
        "reading": {
            "verdict": {
                "action": "transition",
                "to_stage": "shipping",
                "evidence": ["ya salió con el mensajero"],
                "confidence": "high",
            },
            "model": "typesafe/jev-1.13",
            "error": None,
            "answers": [{"id": "estado_pedido.cambio", "choice": "en_camino", "p": 0.93}],
        },
    }


@pytest.mark.asyncio
async def test_registrar_escribe_solo_los_desacuerdos_con_el_estado_anonimizado(_isolate_vault_dir: Path):
    import json

    from src.plugins.order_sentinel.agent.cycle.activities import record_order_sentinel_shadow_activity

    session = _isolate_vault_dir / "wa_573007654321"
    session.mkdir()
    (session / "metadata.json").write_text(
        json.dumps(
            {
                "tag": "HUMANO",
                "episodes": [
                    {"order_id": "order_01SALIO", "order_draft": {"slots": {"nombre_recibe": "Ana María Pérez"}}}
                ],
            }
        ),
        encoding="utf-8",
    )
    shadow = [
        {**_SHADOW[0], "session_id": "wa_573007654321", "order_id": "order_01SALIO"},
        {**_SHADOW[1], "session_id": "wa_573007654321"},
        {**_SHADOW[0], "session_id": "wa_573001234567"},  # no vino en el snapshot: se ignora
    ]

    out = await ActivityEnvironment().run(record_order_sentinel_shadow_activity, shadow, [_conversation()])

    assert out == {"compared": 3, "disagreements": 1}
    [item] = DisagreementLog(_isolate_vault_dir).pending("estado_pedido")
    assert item["session_id"] == "wa_573007654321"
    assert item["rule"] == {"action": "none"}
    assert item["jev"] == {"action": "transition", "to_stage": "shipping"}
    assert item["model"] == "typesafe/jev-1.13"
    assert item["answers"] == [{"id": "estado_pedido.cambio", "choice": "en_camino", "p": 0.93}]
    assert "[2] equipo de la tienda: ya salió con el mensajero" in item["state"]
    assert "Ana" not in item["state"] and "3007654321" not in item["state"], "el estado va anonimizado"
