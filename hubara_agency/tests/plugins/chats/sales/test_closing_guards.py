"""Guardas del cierre (2026-10-07, análisis docs/calidad-llm/cobertura-motor.html).

Antes solo el formulario de envío exigía el sí del cliente y respetaba su
«después». Ahora también:
* `register_order` no registra si el cliente acaba de aplazar (CON-02) ni sin
  una confirmación (CIE-02): la anotada en el episodio, o el «Confirmar» o el
  «sí» de este mismo mensaje (el borrador sin producto no anota la
  confirmación, y un «Confirmar» legítimo no puede quedar frenado). Sin
  episodio no hay con qué juzgar: no frena.
* `present_order_confirmation` no muestra el resumen si el cliente acaba de
  aplazar (CON-02).
* `manage_conversation_tag` no acepta COMPRA_EXITOSA del bot (CIE-06): la pone
  el equipo al verificar el pago, y le manda a Meta una compra (CAPI Purchase).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.tools.order_registration import RegisterOrderTool
from src.plugins.chats.agent.sales.tools.tags import ManageConversationTagTool
from src.plugins.chats.agent.sales.tools.ui_intents import PresentOrderConfirmationTool
from tests.plugins.chats.sales.test_present_order_confirmation_price_truth import FakeCatalog, _present
from tests.plugins.chats.sales.test_register_order_tool import (
    _SAMPLE_ITEMS,
    _SAMPLE_SHIPPING,
    FakeOrderRegistrationPort,
)

KEY = "wa_test_closing_guards"


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


def _seed(vault: Path, *, signal: str | None = None, confirmed: bool = False, extra: dict | None = None) -> Path:
    episode: dict = {"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": None}
    if confirmed:
        episode["order_draft"] = {"slots": {"producto": "cruz-de-vida"}, "confirmed_at_ms": 5, "confirmed_by": "text"}
    md: dict = {"episodes": [episode], "last_inbound_message_id": "wamid.ULTIMO"}
    if signal:
        md["last_inbound_signal"] = {"kind": signal, "at_ms": 9, "message_id": "wamid.ULTIMO", "text": "x"}
    md.update(extra or {})
    path = vault / KEY / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(md, ensure_ascii=False), encoding="utf-8")
    return path


async def _register(vault: Path, ctx: ToolContext) -> tuple[dict, FakeOrderRegistrationPort]:
    port = FakeOrderRegistrationPort()
    tool = RegisterOrderTool(workspace=str(vault), vault_dir=vault, port=port)
    result = json.loads(await tool.execute_with_context(
        ctx, items=_SAMPLE_ITEMS, shipping=_SAMPLE_SHIPPING, payment_method="transfer",
        subtotal_cop=17000, shipping_cop=7900, total_cop=24900,
    ))
    return result, port


@pytest.mark.asyncio
async def test_register_order_respects_a_customer_who_just_deferred(ctx, _isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir, signal="deferral", confirmed=True)

    result, port = await _register(_isolate_vault_dir, ctx)

    assert (result["registered"], result["error_detail"]) == (False, "customer_deferred")
    assert port.calls == []


@pytest.mark.asyncio
async def test_register_order_needs_the_customer_to_have_confirmed(ctx, _isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir)

    result, port = await _register(_isolate_vault_dir, ctx)

    assert (result["registered"], result["error_detail"]) == (False, "purchase_not_confirmed")
    assert "Confirmar" in result["summary"]
    assert port.calls == []


@pytest.mark.asyncio
async def test_a_confirmation_in_this_message_is_enough_even_without_product_in_the_draft(
    ctx, _isolate_vault_dir: Path
) -> None:
    _seed(_isolate_vault_dir, signal="affirmation")

    result, port = await _register(_isolate_vault_dir, ctx)

    assert result["registered"] is True and len(port.calls) == 1


@pytest.mark.asyncio
async def test_register_order_with_a_confirmation_noted_in_the_episode(ctx, _isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir, confirmed=True)

    result, _port = await _register(_isolate_vault_dir, ctx)

    assert result["registered"] is True


@pytest.mark.asyncio
async def test_the_operator_panel_registers_without_the_bot_guard(ctx, _isolate_vault_dir: Path) -> None:
    """El panel (`api/session_actions.py`) usa la misma tool con
    `closing_guard=False`: el operador ya habló con el cliente."""
    _seed(_isolate_vault_dir, signal="deferral")
    port = FakeOrderRegistrationPort()
    tool = RegisterOrderTool(workspace=str(_isolate_vault_dir), vault_dir=_isolate_vault_dir, port=port,
                             closing_guard=False)

    result = json.loads(await tool.execute_with_context(
        ctx, items=_SAMPLE_ITEMS, shipping=_SAMPLE_SHIPPING, payment_method="transfer",
        subtotal_cop=17000, shipping_cop=7900, total_cop=24900,
    ))

    assert result["registered"] is True and len(port.calls) == 1


@pytest.mark.asyncio
async def test_the_order_summary_is_not_shown_to_a_customer_who_just_deferred(ctx, _isolate_vault_dir: Path) -> None:
    path = _seed(_isolate_vault_dir, signal="deferral")
    tool = PresentOrderConfirmationTool(workspace=str(_isolate_vault_dir), catalog=FakeCatalog())

    result = await _present(tool, ctx, 49500)

    assert (result["queued"], result["error"]) == (False, "customer_deferred")
    assert json.loads(path.read_text(encoding="utf-8")).get("pending_ui_intents") in (None, [])


@pytest.mark.asyncio
async def test_the_bot_cannot_tag_compra_exitosa(ctx, tmp_path: Path) -> None:
    path = _seed(tmp_path, confirmed=True, extra={
        "registered_order": {"success": True, "order_id": "order_7", "total_cop": 89000, "currency": "COP"},
        "ctwa_referrals": [{"ctwa_clid": "abc", "source_id": "ad_1"}],
    })
    tool = ManageConversationTagTool(workspace=str(tmp_path), vault_dir=tmp_path)

    result = json.loads(await tool.execute_with_context(ctx, tag="COMPRA_EXITOSA", motivo="pagó"))

    md = json.loads(path.read_text(encoding="utf-8"))
    assert result.get("error") == "human_only_tag"
    assert "CONFIRMADO_PAGO_PENDIENTE" in result["message"]
    assert "capi_outbox" not in md and md.get("tag") != "COMPRA_EXITOSA"


@pytest.mark.asyncio
async def test_register_order_keeps_the_cash_on_delivery_minimum(ctx, _isolate_vault_dir: Path) -> None:
    """Premortem 2026-10-09: contra entrega es desde $45.000 en productos; el
    registro lo aceptaba por debajo si el LLM no pasaba por la tarjeta."""
    _seed(_isolate_vault_dir, confirmed=True)
    port = FakeOrderRegistrationPort()
    tool = RegisterOrderTool(workspace=str(_isolate_vault_dir), vault_dir=_isolate_vault_dir, port=port)

    result = json.loads(await tool.execute_with_context(
        ctx, items=_SAMPLE_ITEMS, shipping=_SAMPLE_SHIPPING, payment_method="cash_on_delivery",
        subtotal_cop=17000, shipping_cop=7900, total_cop=24900,
    ))

    assert (result["registered"], result["error_detail"]) == (False, "cod_below_minimum")
    assert "45.000" in result["summary"]
    assert port.calls == []
