"""El turno de Ventas V2 que sigue a un traspaso de remarketing (caso 2026-10-16).

Remarketing le mandó un gancho a un cliente que un colega había atendido («es
una caja de 12 por ese precio», «¿te hacemos el pedido hoy?»). El cliente
contestó «Si». Remarketing lo entendió y le pasó el chat a Ventas, pero Ventas
solo recibió el resumen: arrancó sin el «Si», sin lo que había dicho el colega
y sin el gancho; su historial terminaba en un «entonces no» del cliente, y el
encuadre del traspaso le ofrecía `NO_MESSAGE` «si no hay mensaje del cliente».
Contestó `NO_MESSAGE` y el colega tuvo que mandar el formulario a mano.

Ahora el turno trae lo que escribió el cliente, tal cual, con la nota de lo que
Ventas no vio (`read_handoff_reply`), y si aun así el modelo contesta
`NO_MESSAGE` tiene una ronda más: el cliente escribió y espera respuesta.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from exoclaw_temporal.config import LLMResponseData
from src.plugins.chats.agent.sales.activities import read_handoff_reply_activity
from src.plugins.chats.agent.sales.contracts import HandoffReply, SalesSessionInput
from src.plugins.chats.agent.sales.workflows.sales_session_v2 import HubaraSalesSessionWorkflowV2
from tests.test_sales_workflow_debounce import SALES_QUEUE, Tracker, _final_resp, _make_fake_activities

pytestmark = pytest.mark.asyncio

_SUMMARY = (
    "Cliente respondió \"Si\" al gancho de reactivación sobre la caja de velas. "
    "Siguiente paso: datos de envío."
)
_REPLY = HandoffReply(
    text=(
        "[Después de tu último mensaje, un colega del equipo le escribió al cliente en este chat "
        "(el cliente lo ve como la misma conversación):\n"
        "- Tu colega: «El precio es por la caja de 12»\n"
        "- Mensaje automático: «Hola de nuevo, ¿te ayudo a cerrar el pedido?»\n"
        "Lo que el cliente escribe ahora puede ser la respuesta a tu colega: sigue desde ahí y no "
        "contradigas lo que le dijo.]\n"
        "Si"
    ),
    customer_text="Si",
    wamids=["wamid.in9"],
    ts_ms=1792184941000,
)
_PRIOR = [
    {"role": "user", "content": "Ah, pensé que venían varias, entonces no"},
    {"role": "assistant", "content": "Claro, el precio es por unidad."},
]
_ANSWER = "¡Listo! Para enviarte tu caja necesito unos datos 🤍"


async def _run_handoff(
    tmp_path: Path,
    *,
    responses: list[LLMResponseData],
    reply: HandoffReply | None = _REPLY,
    signal: list[Any] | None = None,
    real_reply: bool = False,
    writes_while_reading: list[Any] | None = None,
    handoffs: list[str | None] | None = None,
) -> Tracker:
    """Ventas arranca por un traspaso (sin señal del cliente, como en prod) y
    después silencio (el idle cierra la sesión). `real_reply`: la activity
    real lee el chat del vault aislado en vez del fake. `writes_while_reading`:
    la señal del cliente que llega MIENTRAS se lee el chat. `handoffs`: lo que
    devuelve cada lectura del traspaso (por defecto, uno al arrancar)."""
    tracker = Tracker()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    box: dict[str, Any] = {}

    async def customer_writes() -> None:
        await box["handle"].signal(HubaraSalesSessionWorkflowV2.send_message, args=writes_while_reading)

    acts = _make_fake_activities(
        tracker,
        workspace_path=str(workspace),
        prior_history=_PRIOR,
        handoff_sequence=handoffs if handoffs is not None else [_SUMMARY],
        handoff_reply=reply,
        handoff_reply_hook=customer_writes if writes_while_reading is not None else None,
        llm_responses=responses,
    )
    if real_reply:
        acts = [a for a in acts if a.__temporal_activity_definition.name != "read_handoff_reply"]
        acts.append(read_handoff_reply_activity)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=SALES_QUEUE, workflows=[HubaraSalesSessionWorkflowV2], activities=acts
        ):
            handle = await env.client.start_workflow(
                HubaraSalesSessionWorkflowV2.run,
                SalesSessionInput(session_id="wa_handoff", runtime_workspace_path=str(workspace)),
                id="session-wa_handoff",
                task_queue=SALES_QUEUE,
            )
            box["handle"] = handle
            if signal is not None:
                await handle.signal(HubaraSalesSessionWorkflowV2.send_message, args=signal)
            await handle.result()
    return tracker


def _turn_prompts(tracker: Tracker) -> list[Any]:
    return [c for c in tracker.build_prompt_calls if "GHOST" not in c.message]


async def test_the_handoff_turn_answers_what_the_customer_wrote_not_a_summary(tmp_path: Path) -> None:
    tracker = await _run_handoff(tmp_path, responses=[_final_resp(_ANSWER)])

    prompt = _turn_prompts(tracker)[0]
    assert prompt.message == _REPLY.text
    assert "HANDOFF DE REMARKETING A VENTAS" not in prompt.message
    # El resumen de remarketing sigue como contexto del turno.
    assert any("[HANDOFF_REMARKETING]" in c and _SUMMARY in c for c in prompt.plugin_context or [])
    assert [m for (_s, m) in tracker.send_whatsapp_calls] == [_ANSWER]


async def test_without_a_reply_the_handoff_turn_is_as_today(tmp_path: Path) -> None:
    tracker = await _run_handoff(tmp_path, reply=None, responses=[_final_resp("NO_MESSAGE")])

    prompt = _turn_prompts(tracker)[0]
    assert "HANDOFF DE REMARKETING A VENTAS" in prompt.message
    assert tracker.send_whatsapp_calls == []


async def test_a_no_message_to_a_customer_who_wrote_gets_one_more_round(tmp_path: Path) -> None:
    tracker = await _run_handoff(tmp_path, responses=[_final_resp("NO_MESSAGE"), _final_resp(_ANSWER)])

    assert [m for (_s, m) in tracker.send_whatsapp_calls] == [_ANSWER]
    second = tracker.llm_inputs[1]
    assert any(
        m.get("role") == "system" and "espera tu respuesta" in str(m.get("content")) for m in second
    ), second[-2:]


async def test_the_reply_of_the_customer_is_answered_once_even_if_it_also_arrives_by_signal(
    tmp_path: Path,
) -> None:
    tracker = await _run_handoff(
        tmp_path,
        responses=[_final_resp(_ANSWER)],
        signal=["Si", None, None, {"wamid": "wamid.in9", "ts_ms": 1792184941000, "kind": "text", "text": "Si"}],
    )

    turns = _turn_prompts(tracker)
    assert len(turns) == 1, [t.message for t in turns]
    assert turns[0].message.endswith("Si")
    assert [m for (_s, m) in tracker.send_whatsapp_calls] == [_ANSWER]



def _meta(wamid: str, text: str) -> dict[str, Any]:
    return {"wamid": wamid, "ts_ms": 1792184945000, "kind": "text", "text": text}


async def test_the_same_message_signaled_while_the_chat_is_read_goes_once(tmp_path: Path) -> None:
    tracker = await _run_handoff(
        tmp_path,
        responses=[_final_resp(_ANSWER)],
        writes_while_reading=["Si", None, None, _meta("wamid.in9", "Si")],
    )

    turns = _turn_prompts(tracker)
    assert len(turns) == 1
    assert turns[0].message.split("\n").count("Si") == 1, turns[0].message


async def test_what_the_customer_wrote_to_remarketing_goes_before_what_came_after(tmp_path: Path) -> None:
    tracker = await _run_handoff(
        tmp_path,
        responses=[_final_resp(_ANSWER)],
        writes_while_reading=["¿y el envío?", None, None, _meta("wamid.in10", "¿y el envío?")],
    )

    turns = _turn_prompts(tracker)
    assert len(turns) == 1
    assert turns[0].message.endswith("Si\n¿y el envío?"), turns[0].message


async def test_a_second_handoff_does_not_bring_back_what_already_came(tmp_path: Path) -> None:
    # Segundo traspaso en la misma corrida, sin un mensaje de Ventas en medio
    # (p. ej. el primero terminó en NO_MESSAGE): el «Si» ya entró.
    tracker = await _run_handoff(
        tmp_path,
        responses=[_final_resp(_ANSWER), _final_resp(_ANSWER)],
        handoffs=[_SUMMARY, None, "Usuario respondió: hola"],
    )

    assert len(tracker.handoff_reply_calls) == 2, tracker.handoff_reply_calls
    assert "wamid.in9" in tracker.handoff_reply_calls[1]

# --- El caso del 2026-10-16 de punta a punta (datos sintéticos) --------------
# El chat como quedó en el vault: la última respuesta de Ventas, lo que hizo el
# colega mientras tuvo el chat, el gancho de remarketing y el «Si». La activity
# real lo lee; el modelo contesta NO_MESSAGE como en producción y después
# responde.

_INCIDENT_CHAT = [
    {"role": "user", "content": "Ah, pensé que venían varias, entonces no"},
    {"role": "assistant", "content": "Claro, el precio es por unidad.", "tools_used": ["send_reply"]},
    {"role": "assistant", "sender": "human", "content": "Ese precio es por la caja de doce"},
    {"role": "assistant", "sender": "human", "content": "¿Quedó clara la duda?"},
    {"role": "user", "content": "Pero me habías dicho que cada una"},
    {"role": "assistant", "sender": "human", "content": "Son 12 velitas en la caja por ese valor"},
    {"role": "user", "content": "Ah ok"},
    {"role": "assistant", "sender": "human", "content": "¿Te dejo el pedido listo para hoy?"},
    {
        "role": "assistant",
        "agent": "remarketing",
        "content": "Hola otra vez 🤍 Te quedó pendiente la caja de 12 velitas, ¿cerramos el pedido?",
    },
    {"role": "user", "content": "Si", "wamid": "wamid.in9", "timestamp": "2026-10-16T21:09:01+00:00"},
]


async def test_incident_2026_10_16_ventas_answers_the_yes_with_what_the_colleague_said(
    tmp_path: Path, _isolate_vault_dir: Path
) -> None:
    log = _isolate_vault_dir / "wa_handoff" / "sessions" / "wa_handoff.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in _INCIDENT_CHAT), encoding="utf-8")

    tracker = await _run_handoff(
        tmp_path,
        reply=None,
        real_reply=True,
        responses=[_final_resp("NO_MESSAGE"), _final_resp(_ANSWER)],
    )

    seen = tracker.llm_inputs[0][-1]["content"]
    assert seen.endswith("\nSi"), seen
    assert "«Son 12 velitas en la caja por ese valor»" in seen
    assert "«¿Te dejo el pedido listo para hoy?»" in seen
    assert "Te quedó pendiente la caja de 12 velitas" in seen
    assert "NO_MESSAGE" not in seen  # el encuadre que ofrecía callarse no está
    assert [m for (_s, m) in tracker.send_whatsapp_calls] == [_ANSWER]
