"""Envío gratis desde 6 cajas de la campaña: la cuenta del pedido (operador,
2026-10-20).

«Otro gancho de venta: a partir de 6 cajas envío gratis… para que se haga bien
la cuenta en caso de que sí hagan el pedido por 6 cajas o más.»

Hasta hoy el envío era SIEMPRE una tarifa publicada (decisión del 2026-09-23):
el resumen y el registro rechazaban un envío $0. Con 6 cajas o más de los
productos de la campaña (se pueden mezclar), el envío es $0 y el total es el de
los productos; con menos, todo sigue como estaba.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.catalog import ProductNotFoundError
from src.platform.catalog.dtos import CatalogPriceDTO, CatalogProductDTO, CatalogVariantDTO
from src.platform.orders.port import OrderRegistrationResult
from src.plugins.chats.agent.sales.config.shipping import SHIPPING_RATE_BOGOTA_COP
from src.plugins.chats.agent.sales.tools.order_registration import RegisterOrderTool
from src.plugins.chats.agent.sales.tools.ui_intents import PresentOrderConfirmationTool
from src.plugins.chats.shared import sales_campaigns as sc

ONDULADA, CUADRADA = "velas-de-deseos-onduladas", "velas-de-deseos-cuadradas"
TRILOGIA = "trilogia-del-terror"
PRICES = {ONDULADA: 22_500, CUADRADA: 25_000, TRILOGIA: 45_000}
FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "sales_campaigns"


@pytest.fixture(autouse=True)
def _campaign_on(monkeypatch: pytest.MonkeyPatch) -> sc.Campaign:
    (campaign,) = sc.load_campaigns(FIXTURES)
    always = sc.Campaign(**{**vars(campaign), "desde": date(2000, 1, 1), "hasta": date(2999, 1, 1)})
    monkeypatch.setattr(sc, "active_campaigns", lambda: (always,))
    return always


class _Boxes:
    """Las dos cajas de la campaña, sin opciones (la cuenta es lo que se prueba)."""

    async def get_by_handle(self, handle: str) -> CatalogProductDTO:
        if handle not in PRICES:
            raise ProductNotFoundError(handle)
        return CatalogProductDTO(
            id=f"prod_{handle}", handle=handle, title=handle.replace("-", " ").capitalize(), status="published",
            variants=[CatalogVariantDTO(id=f"var_{handle}", title="Unico",
                                        prices=[CatalogPriceDTO(amount=str(PRICES[handle]), currency_code="cop")])],
        )


def _ctx() -> ToolContext:
    return ToolContext(session_key="s_free", channel="whatsapp", chat_id="c")


def _items(onduladas: int, cuadradas: int) -> list[dict]:
    lines = [(ONDULADA, onduladas), (CUADRADA, cuadradas)]
    return [{"handle": h, "quantity": q, "unit_price_cop": PRICES[h]} for h, q in lines if q]


def _subtotal(onduladas: int, cuadradas: int) -> int:
    return onduladas * PRICES[ONDULADA] + cuadradas * PRICES[CUADRADA]


def _intents(vault: Path) -> list[dict]:
    return json.loads((vault / "s_free" / "metadata.json").read_text(encoding="utf-8"))["pending_ui_intents"]


# ── el resumen del pedido ───────────────────────────────────────────────────


async def _confirm(tmp_path: Path, *, onduladas: int, cuadradas: int, shipping_cop: int, method: str) -> dict:
    tool = PresentOrderConfirmationTool(workspace=str(tmp_path), catalog=_Boxes())
    return json.loads(await tool.execute_with_context(
        _ctx(), items=_items(onduladas, cuadradas), shipping_cop=shipping_cop,
        shipping_address_summary="Calle 59b sur 38, Poblado, Medellín", payment_method=method,
    ))


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["cash_on_delivery", "transfer"])
async def test_six_mixed_boxes_confirm_with_free_shipping_and_the_products_total(tmp_path: Path, method: str) -> None:
    result = await _confirm(tmp_path, onduladas=3, cuadradas=3, shipping_cop=0, method=method)

    assert result["queued"] is True, result
    (intent,) = _intents(tmp_path / "isolated_vault")
    params = intent["params"]
    assert (params["shipping_cop"], params["total_cop"]) == (0, _subtotal(3, 3))
    assert params["free_shipping"] == {"campaign": "velitas-7-dic", "min_boxes": 6, "boxes": 6}
    # El modelo sabe que es gratis y, también con contra entrega, el total.
    assert "envío gratis" in result["summary"].lower()
    assert f"{_subtotal(3, 3):,}".replace(",", ".") in result["summary"]


@pytest.mark.asyncio
async def test_six_boxes_with_a_shipping_rate_are_sent_back_to_zero(tmp_path: Path) -> None:
    result = await _confirm(tmp_path, onduladas=6, cuadradas=0, shipping_cop=16_940, method="transfer")

    assert (result["queued"], result.get("error")) == (False, "shipping_mismatch")
    assert "gratis" in result["message"] and "shipping_cop=0" in result["message"]


@pytest.mark.asyncio
async def test_five_boxes_still_pay_the_published_rate(tmp_path: Path) -> None:
    result = await _confirm(tmp_path, onduladas=2, cuadradas=3, shipping_cop=0, method="transfer")

    assert (result["queued"], result.get("error")) == (False, "shipping_mismatch")
    assert "7.900" in result["message"] and "16.940" in result["message"]


@pytest.mark.asyncio
async def test_six_boxes_with_another_product_pay_the_published_rate(tmp_path: Path) -> None:
    """Operador, 2026-10-20: el envío gratis es solo para las cajas de la
    campaña; un pedido que además trae otro producto paga el envío."""
    tool = PresentOrderConfirmationTool(workspace=str(tmp_path), catalog=_Boxes())
    items = [*_items(6, 0), {"handle": TRILOGIA, "quantity": 1, "unit_price_cop": PRICES[TRILOGIA]}]

    result = json.loads(await tool.execute_with_context(
        _ctx(), items=items, shipping_cop=0, shipping_address_summary="Calle 59b sur 38, Poblado, Medellín",
        payment_method="transfer",
    ))

    assert (result["queued"], result.get("error")) == (False, "shipping_mismatch")
    assert "16.940" in result["message"]


# ── la tarjeta que lee el cliente ───────────────────────────────────────────


_FREE = {"campaign": "velitas-7-dic", "min_boxes": 6, "boxes": 6}
_CARD = {
    "reference_id": "HUB-hubara-s_free-1",
    "items": [{"title": "Caja de Velas de deseos onduladas", "quantity": 6, "unit_price_cop": 22_500}],
    "subtotal_cop": 135_000, "shipping_cop": 0, "tax_cop": 0, "total_cop": 135_000, "currency": "COP",
    "shipping_address_summary": "Calle 59b sur 38, Poblado, Medellín", "free_shipping": _FREE,
}


def test_the_cash_on_delivery_card_says_free_shipping_and_what_to_pay() -> None:
    from src.plugins.chats.agent.sales.card_messages import order_card_text

    text = order_card_text({**_CARD, "payment_method": "cash_on_delivery"})

    assert "Envío: gratis (6 cajas o más)" in text
    assert "Por confirmar" not in text and "$135.000" in text.split("Envío")[1]


def test_the_prepaid_card_says_free_shipping() -> None:
    from src.plugins.chats.agent.sales.card_messages import order_card_text

    text = order_card_text({**_CARD, "payment_method": "transfer"})

    assert "Envío: gratis (6 cajas o más)" in text and "Total: $135.000" in text
    assert "tarifa mínima" not in text


# ── el registro del pedido ──────────────────────────────────────────────────


class _Port:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def register_order(self, **kwargs) -> OrderRegistrationResult:
        self.calls.append(kwargs)
        return OrderRegistrationResult(success=True, order_id="order_free", provider="medusa")


_SHIPPING = {"city": "Medellín", "neighborhood": "Poblado", "address": "Calle 59b sur 38",
             "phone": "3001234567", "receiver_name": "Ana Pérez"}


async def _register(vault: Path, port: _Port, *, onduladas: int, cuadradas: int, shipping_cop: int) -> dict:
    tool = RegisterOrderTool(workspace=str(vault), vault_dir=vault, port=port, catalog=_Boxes())
    subtotal = _subtotal(onduladas, cuadradas)
    return json.loads(await tool.execute_with_context(
        _ctx(), items=_items(onduladas, cuadradas), shipping=_SHIPPING, payment_method="transfer",
        subtotal_cop=subtotal, shipping_cop=shipping_cop, total_cop=subtotal + shipping_cop,
    ))


@pytest.mark.asyncio
async def test_six_boxes_register_with_free_shipping(_isolate_vault_dir: Path) -> None:
    port = _Port()

    env = await _register(_isolate_vault_dir, port, onduladas=4, cuadradas=2, shipping_cop=0)

    assert env["registered"] is True, env
    (call,) = port.calls
    assert (call["shipping_cop"], call["total_cop"]) == (0, _subtotal(4, 2))
    assert call["free_shipping"] == {"campaign": "velitas-7-dic", "min_boxes": 6, "boxes": 6}
    record = json.loads((_isolate_vault_dir / "s_free" / "metadata.json").read_text(encoding="utf-8"))
    assert record["registered_order"]["free_shipping"] == call["free_shipping"]


@pytest.mark.asyncio
async def test_six_boxes_with_a_shipping_rate_do_not_register(_isolate_vault_dir: Path) -> None:
    port = _Port()

    env = await _register(_isolate_vault_dir, port, onduladas=6, cuadradas=0, shipping_cop=SHIPPING_RATE_BOGOTA_COP)

    assert (env["registered"], env.get("error")) == (False, "shipping_mismatch")
    assert "gratis" in env["summary"] and "shipping_cop=0" in env["summary"]
    assert port.calls == []


@pytest.mark.asyncio
async def test_five_boxes_without_shipping_do_not_register(_isolate_vault_dir: Path) -> None:
    port = _Port()

    env = await _register(_isolate_vault_dir, port, onduladas=5, cuadradas=0, shipping_cop=0)

    assert (env["registered"], env.get("error")) == (False, "shipping_mismatch")
    assert port.calls == []
    # Con menos de 6 cajas el puerto no recibe la marca (los fakes viejos siguen sirviendo).


@pytest.mark.asyncio
async def test_an_order_with_less_than_six_boxes_does_not_carry_the_mark(_isolate_vault_dir: Path) -> None:
    port = _Port()

    env = await _register(_isolate_vault_dir, port, onduladas=1, cuadradas=1, shipping_cop=16_940)

    assert env["registered"] is True, env
    assert "free_shipping" not in port.calls[0]


# ── las puertas sin LLM: «Crear pedido» (dashboard y app) y el operador ─────


def test_the_shipping_the_system_computes_is_free_with_six_boxes_and_the_rate_otherwise() -> None:
    from src.plugins.chats.agent.sales.config.shipping import order_shipping

    free_cop, free = order_shipping([(ONDULADA, 3), (CUADRADA, 3)], "Medellín")
    rate_cop, none = order_shipping([(ONDULADA, 5)], "Medellín")

    assert (free_cop, free.boxes) == (0, 6)
    assert (rate_cop, none) == (16_940, None)


def test_crear_pedido_registers_six_boxes_without_shipping(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.plugins.chats.api import session_actions
    from src.plugins.chats.api.session_actions import SessionActionsDeps

    port = _Port()

    async def flush(session_key: str) -> int:
        return 1

    async def notify(session_key: str, episode_id: str, closing_tag: str) -> None:
        return None

    deps = SessionActionsDeps(vault_dir=tmp_path, catalog=_Boxes(), order_port=port, flush=flush,
                              notify_episode_closed=notify)
    app = FastAPI()
    app.include_router(session_actions.router, prefix="/api/chats")
    app.dependency_overrides[session_actions.get_session_actions_deps] = lambda: deps
    order = {"items": [{"handle": ONDULADA, "quantity": 6}], "shipping": _SHIPPING, "payment_method": "transfer"}

    r = TestClient(app).post("/api/chats/session-actions/wa_10000000001/order", json=order)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["registered"] is True, body
    assert (body["shipping_cop"], body["total_cop"]) == (0, 6 * PRICES[ONDULADA])
    assert port.calls[0]["free_shipping"]["boxes"] == 6


# ── la tarjeta de tarifas: el gancho cuando preguntan cuánto vale el envío ──
#
# El contrato del turno manda la tarjeta fija de tarifas y termina el turno: en
# una conversación de la campaña, la tarjeta misma trae el envío gratis.


def _seed_episode(vault: Path, **episode) -> None:
    (vault / "s_free").mkdir(parents=True, exist_ok=True)
    (vault / "s_free" / "metadata.json").write_text(
        json.dumps({"episodes": [{"episode_id": "ep_1", "started_at_ms": 1, **episode}]}), encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_in_a_campaign_conversation_the_rates_card_brings_the_free_shipping(tmp_path: Path) -> None:
    from src.plugins.chats.agent.sales.config.shipping import SHIPPING_RATES_MESSAGE
    from src.plugins.chats.agent.sales.tools.ui_intents import SendShippingRatesTool

    _seed_episode(tmp_path / "isolated_vault", sales_campaign="velitas-7-dic")

    result = json.loads(await SendShippingRatesTool(workspace=str(tmp_path)).execute_with_context(_ctx()))

    text = result["customer_text"]
    assert text.startswith(SHIPPING_RATES_MESSAGE) and "6 cajas o más" in text and "envío es gratis" in text
    (intent,) = _intents(tmp_path / "isolated_vault")
    assert intent["params"] == {"campaign": "velitas-7-dic"}  # nunca texto: el flush lo arma


@pytest.mark.asyncio
async def test_out_of_the_campaign_the_rates_card_is_the_usual_one(tmp_path: Path) -> None:
    from src.plugins.chats.agent.sales.config.shipping import SHIPPING_RATES_MESSAGE
    from src.plugins.chats.agent.sales.tools.ui_intents import SendShippingRatesTool

    _seed_episode(tmp_path / "isolated_vault")

    result = json.loads(await SendShippingRatesTool(workspace=str(tmp_path)).execute_with_context(_ctx()))

    assert result["customer_text"] == SHIPPING_RATES_MESSAGE


@pytest.mark.asyncio
async def test_the_flush_sends_the_rates_text_the_tool_wrote() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.platform.whatsapp import dtos as wa_dtos
    from src.plugins.chats.agent.sales.activities.flush_ui_intents import _build_history_event, _dispatch_intent

    from src.plugins.chats.agent.sales.config.shipping import SHIPPING_RATES_MESSAGE

    wa = SimpleNamespace(send_text=AsyncMock(return_value=SimpleNamespace(ok=True)))
    params = {"campaign": "velitas-7-dic"}

    await _dispatch_intent(wa_client=wa, wa_dtos=wa_dtos, kind="shipping_rates", params=params, fallback={},
                           phone_number_id="phone-1", to_number="10000000001", last_inbound_message_id=None)

    sent = wa.send_text.await_args.args[2]
    assert sent.startswith(SHIPPING_RATES_MESSAGE) and "con 6 cajas o más" in sent
    assert _build_history_event("shipping_rates", params)["content"] == sent

