"""Productores del embudo CAPI dentro del turno de Sales (auditoría 2026-09-08, punto 6).

Cada momento determinista del embudo encola su evento en
``metadata["capi_outbox"]``; el envío lo hace el flusher único. Mapa:

  INTERESADO (tag)                  → QualifiedLead
  product_detail / lista / galería  → ViewContent   (al ENVIARSE, no al encolarse)
  order_confirmation enviado        → AddToCart     (con value = total)
  shipping_flow enviado             → InitiateCheckout
  register_order OK                 → OrderCreated  (con value = total)
  COMPRA_EXITOSA (tag)              → Purchase      (mismo id que el cierre humano)
  TIMEOUT con pedido armado         → CartAbandoned

Sesión orgánica (sin ``ctwa_clid``) → ningún productor escribe nada.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from exoclaw.agent.tools import ToolContext

from src.plugins.chats.agent.sales.tools.tags import ManageConversationTagTool
from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    EPISODE_TIMEOUT_MS,
    ensure_active_episode,
)

SESSION = "wa_573001234567"
NOW_MS = 1_757_350_000_000


def _ctx(session: str = SESSION) -> ToolContext:
    return ToolContext(session_key=session, channel="whatsapp", chat_id=session)


def _attributed(**extra: Any) -> dict[str, Any]:
    md: dict[str, Any] = {
        "phone_number_id": "pnid-1",
        "ctwa_referrals": [{"ctwa_clid": "CLID_1", "captured_at_ms": NOW_MS - 1000}],
        "episodes": [{"episode_id": "ep_001", "started_at_ms": NOW_MS - 5000, "closed_at_ms": None}],
    }
    md.update(extra)
    return md


def _seed(vault: Path, md: dict[str, Any], session: str = SESSION) -> Path:
    path = vault / session / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(md), encoding="utf-8")
    return path


def _outbox(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8")).get("capi_outbox", [])


def _names(path: Path) -> list[str]:
    return [e["event_name"] for e in _outbox(path)]


# =============================================================================
# manage_conversation_tag
# =============================================================================


@pytest.mark.asyncio
async def test_interesado_enqueues_qualified_lead(tmp_path: Path) -> None:
    path = _seed(tmp_path, _attributed())
    tool = ManageConversationTagTool(workspace=str(tmp_path), vault_dir=tmp_path)
    await tool.execute_with_context(_ctx(), tag="INTERESADO", motivo="pidió precio")
    entries = _outbox(path)
    assert [e["event_name"] for e in entries] == ["QualifiedLead"]
    assert entries[0]["event_id"] == f"qualifiedlead_{SESSION}_ep_001"
    assert entries[0]["source"] == "manage_conversation_tag"


@pytest.mark.asyncio
async def test_interesado_twice_enqueues_once(tmp_path: Path) -> None:
    path = _seed(tmp_path, _attributed())
    tool = ManageConversationTagTool(workspace=str(tmp_path), vault_dir=tmp_path)
    await tool.execute_with_context(_ctx(), tag="INTERESADO", motivo="a")
    await tool.execute_with_context(_ctx(), tag="INTERESADO", motivo="b")
    assert _names(path) == ["QualifiedLead"]


@pytest.mark.asyncio
async def test_compra_exitosa_enqueues_purchase_with_order_value(tmp_path: Path) -> None:
    path = _seed(
        tmp_path,
        _attributed(registered_order={"success": True, "order_id": "order_7", "total_cop": 89000, "currency": "COP"}),
    )
    tool = ManageConversationTagTool(workspace=str(tmp_path), vault_dir=tmp_path)
    await tool.execute_with_context(_ctx(), tag="COMPRA_EXITOSA", motivo="pago verificado")
    entries = _outbox(path)
    assert [(e["event_name"], e["event_id"], e["value"], e["currency"]) for e in entries] == [
        ("Purchase", "purchase_order_7", 89000, "COP")
    ]


@pytest.mark.asyncio
async def test_organic_session_enqueues_nothing(tmp_path: Path) -> None:
    md = _attributed()
    md.pop("ctwa_referrals")
    path = _seed(tmp_path, md)
    tool = ManageConversationTagTool(workspace=str(tmp_path), vault_dir=tmp_path)
    await tool.execute_with_context(_ctx(), tag="INTERESADO", motivo="x")
    assert "capi_outbox" not in json.loads(path.read_text(encoding="utf-8"))


# =============================================================================
# register_order
# =============================================================================


@dataclass
class _FakePort:
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def register_order(self, **kwargs: Any) -> Any:
        from src.platform.orders.port import OrderRegistrationResult

        self.calls.append(kwargs)
        return OrderRegistrationResult(
            success=True, order_id="draft_777", provider="fake", customer_id="cus_1",
            error_detail=None, raw_payload={},
        )


@pytest.mark.asyncio
async def test_register_order_success_enqueues_order_created(tmp_path: Path) -> None:
    from src.plugins.chats.agent.sales.tools.order_registration import RegisterOrderTool

    path = _seed(tmp_path, _attributed())
    tool = RegisterOrderTool(workspace=str(tmp_path), vault_dir=tmp_path, port=_FakePort())
    result = json.loads(
        await tool.execute_with_context(
            _ctx(),
            items=[{"handle": "plegaria-de-luz", "quantity": 2, "unit_price_cop": 29000}],
            shipping={
                "city": "Bogotá", "neighborhood": "Chapinero", "address": "Cra 1 # 2-3",
                "phone": "573001234567", "receiver_name": "Ana Pérez",
            },
            payment_method="transfer",
            subtotal_cop=58000,
            shipping_cop=0,
            total_cop=58000,
        )
    )
    assert result["registered"] is True
    entries = _outbox(path)
    assert [(e["event_name"], e["event_id"], e["value"]) for e in entries] == [
        ("OrderCreated", "ordercreated_draft_777", 58000)
    ]
    assert entries[0]["order_id"] == "draft_777"


# =============================================================================
# flush_pending_ui_intents — el momento en que el cliente VE algo
# =============================================================================


def _ok(wamid: str = "wamid.1") -> SimpleNamespace:
    return SimpleNamespace(ok=True, wa_message_id=wamid, error=None)


@pytest.fixture
def flush_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from src.platform import config
    from src.platform.whatsapp import client as wa_client

    monkeypatch.setattr(config, "WORKSPACE_VAULT_DIR", tmp_path)
    monkeypatch.delenv("META_FLOW_ID_SHIPPING", raising=False)
    sends = {
        "send_image": AsyncMock(return_value=_ok("wamid.img")),
        "send_interactive_buttons": AsyncMock(return_value=_ok("wamid.btn")),
        "send_text": AsyncMock(return_value=_ok("wamid.txt")),
        "send_interactive_list": AsyncMock(return_value=_ok("wamid.list")),
    }
    for name, mock in sends.items():
        monkeypatch.setattr(wa_client, name, mock)
    return tmp_path, sends


def _intent(kind: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"{kind}-1", "kind": kind, "params": params, "queued_at_ms": 4_000_000_000_000, "analytics": {}}


@pytest.mark.asyncio
async def test_product_detail_sent_enqueues_view_content(flush_env) -> None:
    from src.plugins.chats.agent.sales.activities import flush_ui_intents

    vault, sends = flush_env
    path = _seed(vault, _attributed(pending_ui_intents=[
        _intent("product_detail", {"image_url": "https://x/y.jpg", "caption": "Plegaria", "handle": "plegaria-de-luz"}),
    ]))
    assert await flush_ui_intents.flush_pending_ui_intents(SESSION) == 1
    entries = _outbox(path)
    assert [(e["event_name"], e["event_id"]) for e in entries] == [("ViewContent", f"viewcontent_{SESSION}_ep_001")]
    assert entries[0]["source"] == "flush_ui_intents:product_detail"


@pytest.mark.asyncio
async def test_failed_send_does_not_enqueue(flush_env) -> None:
    from src.plugins.chats.agent.sales.activities import flush_ui_intents

    vault, sends = flush_env
    sends["send_image"].return_value = SimpleNamespace(ok=False, wa_message_id=None, error="boom")
    path = _seed(vault, _attributed(pending_ui_intents=[
        _intent("product_detail", {"image_url": "https://x/y.jpg", "caption": "P", "handle": "h"}),
    ]))
    await flush_ui_intents.flush_pending_ui_intents(SESSION)
    assert _names(path) == []


@pytest.mark.asyncio
async def test_order_confirmation_sent_enqueues_add_to_cart_with_total(flush_env) -> None:
    from src.plugins.chats.agent.sales.activities import flush_ui_intents

    vault, _ = flush_env
    path = _seed(vault, _attributed(pending_ui_intents=[
        _intent("order_confirmation", {
            "reference_id": "ref-1",
            "items": [{"handle": "plegaria-de-luz", "title": "Plegaria", "quantity": 2, "unit_price_cop": 29000}],
            "subtotal_cop": 58000, "shipping_cop": 7900, "tax_cop": 0, "total_cop": 65900,
            "currency": "COP", "shipping_address_summary": "Bogotá", "payment_method": "transfer",
        }),
    ]))
    assert await flush_ui_intents.flush_pending_ui_intents(SESSION) == 1
    entries = _outbox(path)
    assert [(e["event_name"], e["value"], e["currency"]) for e in entries] == [("AddToCart", 65900, "COP")]


@pytest.mark.asyncio
async def test_shipping_flow_sent_enqueues_initiate_checkout(flush_env) -> None:
    from src.plugins.chats.agent.sales.activities import flush_ui_intents

    vault, _ = flush_env
    path = _seed(vault, _attributed(pending_ui_intents=[
        _intent("shipping_flow", {"flow_id": "FLOW_ID_SHIPPING_PLACEHOLDER", "flow_token": "shipping_x", "header_text": "Datos de envío", "body_text": "Completa"}),
    ]))
    assert await flush_ui_intents.flush_pending_ui_intents(SESSION) == 1
    assert _names(path) == ["InitiateCheckout"]


# =============================================================================
# TIMEOUT lazy close → CartAbandoned
# =============================================================================


def test_timeout_close_with_open_order_enqueues_cart_abandoned() -> None:
    md = _attributed()
    md["episodes"][0]["order_draft"] = {"slots": {"producto": "Plegaria de Luz", "cantidad": "2"}, "updated_at_ms": NOW_MS}
    ensure_active_episode(md, now_ms=NOW_MS + EPISODE_TIMEOUT_MS + 1, session_id=SESSION)
    assert md["episodes"][0]["closing_tag"] == "TIMEOUT"
    assert [(e["event_name"], e["event_id"]) for e in md["capi_outbox"]] == [
        ("CartAbandoned", f"cartabandoned_{SESSION}_ep_001")
    ]


def test_timeout_close_without_cart_enqueues_nothing() -> None:
    md = _attributed()
    ensure_active_episode(md, now_ms=NOW_MS + EPISODE_TIMEOUT_MS + 1, session_id=SESSION)
    assert md["episodes"][0]["closing_tag"] == "TIMEOUT"
    assert "capi_outbox" not in md


def test_timeout_close_after_purchase_enqueues_nothing() -> None:
    # Ya compró: el episodio viejo con order_id y tag COMPRA_EXITOSA no es un carrito.
    md = _attributed(capi_terminal_event="Purchase")
    md["episodes"][0]["order_id"] = "order_1"
    md["episodes"][0]["order_draft"] = {"slots": {"producto": "X"}}
    ensure_active_episode(md, now_ms=NOW_MS + EPISODE_TIMEOUT_MS + 1, session_id=SESSION)
    assert "capi_outbox" not in md
