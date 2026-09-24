"""Guardas deterministas alrededor de la confirmación de compra (2026-09-14,
runs 01a0a0eb / 01a0a0f1): sin un "sí" del cliente no se piden datos de envío,
no se cierra como CONFIRMADO_SIN_DATOS ni se escala a humano; y un cliente que
APLAZA recibe texto, no un formulario."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO
from src.plugins.chats.agent.sales.tools.ui_intents import RequestShippingDetailsTool


class _Catalog:
    """Cubo Love a $21.000 — el precio lo pone el catálogo, no el LLM (run ebbc203d)."""

    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        assert handle == "cubo-love", handle
        return CatalogProductDTO(
            id="prod_cubo", handle="cubo-love", title="Cubo Love", status="published",
            variants=[CatalogVariantDTO(
                id="variant_cubo", title="Unico",
                prices=[CatalogPriceDTO(amount="21000", currency_code="cop")],
            )],
        )

NOW = 1_789_406_554_683
KEY = "wa_test_guards"


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


def _episode(slots: dict[str, Any] | None = None, confirmed: bool = False) -> dict[str, Any]:
    ep: dict[str, Any] = {"episode_id": "ep_001", "started_at_ms": NOW - 5000, "closed_at_ms": None}
    if slots is not None:
        ep["order_draft"] = {"slots": slots, "updated_at_ms": NOW - 1000}
        if confirmed:
            ep["order_draft"]["confirmed_at_ms"] = NOW - 500
            ep["order_draft"]["confirmed_by"] = "text"
    return ep


def _seed(vault: Path, md: dict[str, Any], key: str = KEY) -> Path:
    path = vault / key / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(md, ensure_ascii=False), encoding="utf-8")
    return path


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- shipping


@pytest.mark.asyncio
async def test_shipping_details_rejected_without_purchase_confirmation(ctx, _isolate_vault_dir: Path) -> None:
    path = _seed(_isolate_vault_dir, {"episodes": [_episode({"producto": "Cubo Love", "color": "Azul", "aroma": "Café"})]})
    tool = RequestShippingDetailsTool(workspace=str(_isolate_vault_dir), catalog=_Catalog())
    result = json.loads(await tool.execute_with_context(ctx, items=[{"handle": "cubo-love", "quantity": 1}]))
    assert result["queued"] is False
    assert result["error"] == "purchase_not_confirmed"
    assert "Cubo Love" in result["message"]
    assert "21.000" in result["message"] or "21000" in result["message"]
    assert _read(path).get("pending_ui_intents", []) == []


@pytest.mark.asyncio
async def test_shipping_details_rejected_when_customer_just_deferred(ctx, _isolate_vault_dir: Path) -> None:
    md = {
        "last_inbound_message_id": "wamid.defer",
        "last_inbound_signal": {"kind": "deferral", "at_ms": NOW, "message_id": "wamid.defer", "text": "Voy apenas en camino a casa"},
        "episodes": [_episode({"producto": "Cubo Love", "color": "Azul"}, confirmed=True)],
    }
    path = _seed(_isolate_vault_dir, md)
    tool = RequestShippingDetailsTool(workspace=str(_isolate_vault_dir), catalog=_Catalog())
    result = json.loads(await tool.execute_with_context(ctx, items=[{"handle": "cubo-love", "quantity": 1}]))
    assert result["queued"] is False
    assert result["error"] == "customer_deferred"
    assert "en camino" in result["message"]
    assert _read(path).get("pending_ui_intents", []) == []


@pytest.mark.asyncio
async def test_shipping_details_allowed_after_confirmation(ctx, _isolate_vault_dir: Path) -> None:
    path = _seed(_isolate_vault_dir, {"last_inbound_message_id": "wamid.yes", "episodes": [_episode({"producto": "Cubo Love", "color": "Azul"}, confirmed=True)]})
    tool = RequestShippingDetailsTool(workspace=str(_isolate_vault_dir), catalog=_Catalog())
    result = json.loads(await tool.execute_with_context(ctx, items=[{"handle": "cubo-love", "quantity": 1}]))
    assert result["queued"] is True
    assert [i["kind"] for i in _read(path)["pending_ui_intents"]] == ["shipping_flow"]


@pytest.mark.asyncio
async def test_shipping_details_allowed_with_registered_order(ctx, _isolate_vault_dir: Path) -> None:
    _seed(_isolate_vault_dir, {"registered_order": {"success": True, "order_id": "o1"}, "episodes": [_episode()]})
    tool = RequestShippingDetailsTool(workspace=str(_isolate_vault_dir), catalog=_Catalog())
    result = json.loads(await tool.execute_with_context(ctx, items=[{"handle": "cubo-love", "quantity": 1}]))
    assert result["queued"] is True


# ---------------------------------------------------------------- tag


@pytest.mark.asyncio
async def test_confirmado_sin_datos_degrades_to_interesado_without_confirmation(ctx, tmp_path: Path) -> None:
    from src.plugins.chats.agent.sales.tools.tags import ManageConversationTagTool

    path = _seed(tmp_path, {"episodes": [_episode({"producto": "Cubo Love", "color": "Azul"})]})
    tool = ManageConversationTagTool(workspace=str(tmp_path), vault_dir=tmp_path)
    result = json.loads(await tool.execute_with_context(ctx, tag="CONFIRMADO_SIN_DATOS", motivo="pidió datos y no llegaron"))
    md = _read(path)
    assert md["tag"] == "INTERESADO"
    assert md["status_history"][-1]["tag"] == "INTERESADO"
    assert "sin confirmación" in md["motivo"].lower()
    assert result["degraded_from"] == "CONFIRMADO_SIN_DATOS"
    assert "escalate_to_human" in result["message"]
    assert "episode_closed" not in result
    assert md["episodes"][0]["closed_at_ms"] is None


@pytest.mark.asyncio
async def test_confirmado_sin_datos_kept_with_confirmation(ctx, tmp_path: Path) -> None:
    from src.plugins.chats.agent.sales.tools.tags import ManageConversationTagTool

    path = _seed(tmp_path, {"episodes": [_episode({"producto": "Cubo Love"}, confirmed=True)]})
    tool = ManageConversationTagTool(workspace=str(tmp_path), vault_dir=tmp_path)
    result = json.loads(await tool.execute_with_context(ctx, tag="CONFIRMADO_SIN_DATOS", motivo="confirmó y no mandó datos"))
    assert _read(path)["tag"] == "CONFIRMADO_SIN_DATOS"
    assert result["episode_closed"]["closing_tag"] == "CONFIRMADO_SIN_DATOS"


# ---------------------------------------------------------------- escalate


@pytest.mark.asyncio
async def test_escalation_for_pending_shipping_rejected_without_confirmation(ctx, tmp_path: Path) -> None:
    from src.platform.tools.escalation import EscalateToHumanTool
    from src.plugins.chats.agent.sales.tools.escalation import guarded_escalation_tool

    SalesEscalateToHumanTool = guarded_escalation_tool(EscalateToHumanTool)

    path = _seed(tmp_path, {"active_route": "ventas", "episodes": [_episode({"producto": "Cubo Love"})]})
    tool = SalesEscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)
    result = json.loads(await tool.execute_with_context(ctx, reason_category="ORDER_PENDING_SHIPPING_DETAILS", summary="no mandó datos"))
    assert "escalation_decision" not in result
    assert result["error"].startswith("precondition_failed")
    assert _read(path)["active_route"] == "ventas"


@pytest.mark.asyncio
async def test_escalation_for_pending_shipping_allowed_with_confirmation(ctx, tmp_path: Path) -> None:
    from src.platform.tools.escalation import EscalateToHumanTool
    from src.plugins.chats.agent.sales.tools.escalation import guarded_escalation_tool

    SalesEscalateToHumanTool = guarded_escalation_tool(EscalateToHumanTool)

    path = _seed(tmp_path, {"active_route": "ventas", "episodes": [_episode({"producto": "Cubo Love"}, confirmed=True)]})
    tool = SalesEscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)
    result = json.loads(await tool.execute_with_context(ctx, reason_category="ORDER_PENDING_SHIPPING_DETAILS", summary="confirmó, faltan datos"))
    assert result["escalation_decision"]["reason_category"] == "ORDER_PENDING_SHIPPING_DETAILS"
    assert _read(path)["active_route"] == "humano"


@pytest.mark.asyncio
async def test_other_escalation_reasons_are_untouched(ctx, tmp_path: Path) -> None:
    from src.platform.tools.escalation import EscalateToHumanTool
    from src.plugins.chats.agent.sales.tools.escalation import guarded_escalation_tool

    SalesEscalateToHumanTool = guarded_escalation_tool(EscalateToHumanTool)

    _seed(tmp_path, {"active_route": "ventas", "episodes": [_episode()]})
    tool = SalesEscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)
    result = json.loads(await tool.execute_with_context(ctx, reason_category="EXPLICIT_REQUEST", summary="pide humano"))
    assert result["escalation_decision"]["reason_category"] == "EXPLICIT_REQUEST"


@pytest.mark.asyncio
async def test_sales_escalation_forwards_the_customer_farewell(ctx, tmp_path: Path) -> None:
    """Run 5ed9af2d: la despedida del relevo viaja en `customer_message`; la
    guarda de Sales la reenvía intacta a la tool de plataforma."""
    from src.platform.tools.escalation import EscalateToHumanTool
    from src.plugins.chats.agent.sales.tools.escalation import guarded_escalation_tool

    SalesEscalateToHumanTool = guarded_escalation_tool(EscalateToHumanTool)

    _seed(tmp_path, {"active_route": "ventas", "episodes": [_episode()]})
    tool = SalesEscalateToHumanTool(workspace=str(tmp_path), vault_dir=tmp_path)
    farewell = "Eso te lo coordino con un colega del equipo, te responde en este mismo chat 🤍"
    result = json.loads(
        await tool.execute_with_context(
            ctx,
            reason_category="BULK_ORDER",
            summary="pide ~100 unidades",
            customer_message=farewell,
        )
    )
    assert result["customer_message"] == farewell


# ---------------------------------------------------------------- remarketing handoff


@pytest.mark.asyncio
async def test_remarketing_transfer_marks_deferral_in_the_handoff(ctx, tmp_path: Path) -> None:
    from src.platform.tools.routing import TransferToSalesAgentTool
    from src.plugins.chats.agent.remarketing.tools import deferral_aware_transfer_tool

    RemarketingTransferToSalesTool = deferral_aware_transfer_tool(TransferToSalesAgentTool)

    md = {
        "active_route": "remarketing",
        "last_inbound_message_id": "wamid.defer",
        "last_inbound_signal": {"kind": "deferral", "at_ms": NOW, "message_id": "wamid.defer", "text": "Voy apenas en camino a casa"},
        "episodes": [_episode({"producto": "Cubo Love", "color": "Azul"})],
    }
    path = _seed(tmp_path, md)
    tool = RemarketingTransferToSalesTool(workspace=str(tmp_path), vault_dir=tmp_path)
    result = json.loads(await tool.execute_with_context(ctx, resumen="Cliente escribió \"Voy apenas en camino a casa\". Siguiente paso: confirmar el pedido y tomar datos de envío."))
    summary = result["transfer_decision"]["summary"]
    assert summary.startswith("[EL CLIENTE APLAZÓ]")
    assert "NO pidas datos de envío" in summary
    assert "Voy apenas en camino a casa" in summary
    assert _read(path)["active_route"] == "ventas"


@pytest.mark.asyncio
async def test_remarketing_transfer_without_deferral_passes_summary_through(ctx, tmp_path: Path) -> None:
    from src.platform.tools.routing import TransferToSalesAgentTool
    from src.plugins.chats.agent.remarketing.tools import deferral_aware_transfer_tool

    RemarketingTransferToSalesTool = deferral_aware_transfer_tool(TransferToSalesAgentTool)

    _seed(tmp_path, {"active_route": "remarketing", "last_inbound_message_id": "wamid.x", "episodes": [_episode()]})
    tool = RemarketingTransferToSalesTool(workspace=str(tmp_path), vault_dir=tmp_path)
    result = json.loads(await tool.execute_with_context(ctx, resumen="Cliente escribió \"Dame 2\". Siguiente paso: datos de envío."))
    assert result["transfer_decision"]["summary"] == "Cliente escribió \"Dame 2\". Siguiente paso: datos de envío."


# ---------------------------------------------------------------- ingest


class _History:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def append_user_event(self, session_id: str, content: str, *, image_url: str | None = None, **_: object) -> None:
        self.events.append((session_id, content))


class _Loader:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def execute(self, session_id: str, message: str, phone_number_id: str | None, extra_context: list[str] | None = None, inbound_meta: dict | None = None) -> None:
        self.calls.append((session_id, message, extra_context or []))


class _Store:
    def __init__(self, initial: dict[str, dict[str, Any]] | None = None) -> None:
        self.store: dict[str, dict[str, Any]] = dict(initial or {})

    def read(self, session_id: str) -> dict[str, Any]:
        return json.loads(json.dumps(self.store.get(session_id, {})))

    def write(self, session_id: str, data: dict[str, Any]) -> None:
        self.store[session_id] = json.loads(json.dumps(data))

    def update(self, session_id: str, mutator):
        fresh = self.read(session_id)
        result = mutator(fresh)
        if result is None:
            return None
        self.write(session_id, result)
        return result


def _ingest(store: _Store):
    from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import IngestInboundMessage

    history = _History()
    loader = _Loader()
    use_case = IngestInboundMessage(history_store=history, load_session=loader, metadata_store=store)  # type: ignore[arg-type]
    return use_case, loader


def _msg(text: str, message_id: str = "wamid.new"):
    from src.plugins.chats.agent.sales.parsers import WhatsAppMessage

    return WhatsAppMessage(message_id=message_id, from_number="573000000001", phone_number_id="PID", text=text, media=None, timestamp="1789406554")


@pytest.mark.asyncio
async def test_ingest_registers_deferral_and_tells_the_llm(_isolate_vault_dir: Path) -> None:
    sid = "wa_573000000001"
    store = _Store({sid: {"episodes": [_episode({"producto": "Cubo Love", "color": "Azul"})]}})
    use_case, loader = _ingest(store)
    await use_case.execute(_msg("Voy apenas en camino a casa", "wamid.defer"))
    md = store.read(sid)
    assert md["last_inbound_signal"]["kind"] == "deferral"
    assert md["last_inbound_signal"]["message_id"] == "wamid.defer"
    assert md["last_inbound_message_id"] == "wamid.defer"
    notes = loader.calls[0][2]
    assert any("APLAZ" in n.upper() and "datos de envío" in n for n in notes), notes


@pytest.mark.asyncio
async def test_ingest_registers_affirmation_as_confirmation(_isolate_vault_dir: Path) -> None:
    sid = "wa_573000000001"
    store = _Store({sid: {"episodes": [_episode({"producto": "Cubo Love", "color": "Azul"})]}})
    use_case, loader = _ingest(store)
    await use_case.execute(_msg("sí, dale", "wamid.yes"))
    md = store.read(sid)
    assert md["episodes"][0]["order_draft"]["confirmed_at_ms"] > 0
    assert not any("APLAZ" in n.upper() for n in loader.calls[0][2])
