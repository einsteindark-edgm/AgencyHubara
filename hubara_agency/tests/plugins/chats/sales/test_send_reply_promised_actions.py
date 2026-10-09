"""`send_reply` graba lo que el texto promete y el turno todavía no hizo.

Incidente del 2026-10-09 (bot V2, cliente sin teléfono): «Perfecto, contra
entrega… Te paso el formulario para los datos de envío 🤍» salió por
`send_reply` dos turnos seguidos y `request_shipping_details` nunca se llamó.
El texto estaba bien; faltaba la acción.

La tool NO decide sola (no ve las otras tools de su mismo paso, que pueden
correr después que ella): devuelve el texto validado como siempre y, en
`promises`, lo que promete y el estado todavía no cumple. Lo cumple un
componente ya encolado en este turno (`pending_ui_intents`: el flush va
después del texto) o, para «tu pedido quedó registrado», una orden
registrada. El turno (`run_agent_turn`) lee ese resultado GRABADO después del
paso completo y, si ninguna tool del turno lo cumplió, retiene el texto y da
UNA ronda más con la tool que falta.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.tools.reply import SendReplyTool

KEY = "wa_test_promises"
FORM_PROMISE = "Perfecto, contra entrega.\n\nTe paso el formulario para los datos de envío 🤍"


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


def _seed(vault: Path, **metadata: Any) -> Path:
    path = vault / KEY / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_inbound_message_id": "wamid.in1", **metadata}), encoding="utf-8")
    return path


async def _send(vault: Path, ctx: ToolContext, text: str) -> dict:
    tool = SendReplyTool(workspace=str(vault), vault_dir=vault)
    return json.loads(await tool.execute_with_context(ctx, text=text))


@pytest.mark.asyncio
async def test_a_promised_form_that_is_not_queued_is_recorded_with_its_tool(tmp_path: Path, ctx: ToolContext) -> None:
    _seed(tmp_path)

    env = await _send(tmp_path, ctx, FORM_PROMISE)

    assert env["reply"] == {"text": FORM_PROMISE}
    (promise,) = env["promises"]
    assert promise["kind"] == "formulario"
    assert promise["tools"] == ["request_shipping_details"]
    assert "request_shipping_details" in promise["nudge"]


@pytest.mark.asyncio
async def test_a_component_already_queued_keeps_the_promise(tmp_path: Path, ctx: ToolContext) -> None:
    _seed(tmp_path, pending_ui_intents=[{"id": "f1", "kind": "shipping_flow", "params": {}}])

    env = await _send(tmp_path, ctx, FORM_PROMISE)

    assert env["reply"] == {"text": FORM_PROMISE}
    assert "promises" not in env


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "kind", "tool"),
    [
        ("Te comparto las tarifas de envío 🚚", "shipping_rates", "send_shipping_rates"),
        ("Listo, te paso el resumen de tu pedido.", "order_confirmation", "present_order_confirmation"),
        ("¡Claro! Te muestro nuestro catálogo 🤍", "categories", "present_products"),
        ("Te envío las fotos del Velón Koala.", "product_gallery", "present_product_detail"),
        ("Te muestro los aromas disponibles.", "variant_picker", "present_variant_picker"),
    ],
)
async def test_each_component_is_kept_by_its_own_card(
    tmp_path: Path, ctx: ToolContext, text: str, kind: str, tool: str
) -> None:
    _seed(tmp_path)
    (promise,) = (await _send(tmp_path, ctx, text))["promises"]
    assert tool in promise["tools"]

    _seed(tmp_path, pending_ui_intents=[{"id": "c1", "kind": kind, "params": {}}])
    assert "promises" not in await _send(tmp_path, ctx, text)


@pytest.mark.asyncio
async def test_saying_the_order_is_registered_needs_a_registered_order(tmp_path: Path, ctx: ToolContext) -> None:
    text = "¡Listo! Tu pedido quedó registrado 🤍"
    _seed(tmp_path)
    (promise,) = (await _send(tmp_path, ctx, text))["promises"]
    assert promise["kind"] == "registro" and promise["tools"] == ["register_order"]

    _seed(tmp_path, registered_order={"success": True, "order_id": "order_1"})
    assert "promises" not in await _send(tmp_path, ctx, text)


@pytest.mark.asyncio
async def test_a_question_or_an_offer_promises_nothing(tmp_path: Path, ctx: ToolContext) -> None:
    _seed(tmp_path)

    asked = await _send(tmp_path, ctx, "Perfecto 🤍 ¿Te paso el formulario para los datos de envío?")
    offered = await _send(tmp_path, ctx, "Si quieres te envío las fotos del Velón Koala.")

    assert "promises" not in asked and "promises" not in offered


@pytest.mark.asyncio
async def test_without_a_vault_the_promise_is_still_recorded(tmp_path: Path, ctx: ToolContext) -> None:
    """Sin vault no hay cola que mirar: la promesa queda grabada y el turno
    decide con las tools que de verdad salieron."""
    tool = SendReplyTool(workspace=str(tmp_path))

    env = json.loads(await tool.execute_with_context(ctx, text=FORM_PROMISE))

    assert env["reply"] == {"text": FORM_PROMISE}
    assert [p["kind"] for p in env["promises"]] == ["formulario"]


@pytest.mark.asyncio
async def test_a_form_already_delivered_in_this_episode_keeps_the_promise(tmp_path: Path, ctx: ToolContext) -> None:
    """Revisión del premortem (2026-10-09): mirar solo la cola hacía que la
    ronda pidiera un SEGUNDO formulario cuando el bot volvía a nombrar el que
    el cliente ya tenía."""
    path = _seed(tmp_path, episodes=[{"episode_id": "ep_001", "started_at_ms": 1_000, "closed_at_ms": None}])
    row = {"id": "f1", "kind": "shipping_flow", "ok": True, "wamid": "wamid.f1", "at_ms": 5_000}
    (path.parent / "ui_intents_delivered.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    env = await _send(tmp_path, ctx, "Te paso el formulario para los datos de envío 🤍")

    assert "promises" not in env
