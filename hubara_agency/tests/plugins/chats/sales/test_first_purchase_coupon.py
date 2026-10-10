"""El cupón de bienvenida (solo primera compra) en el bot de ventas.

Caso del 2026-10-09: la clienta pidió «el descuento por primera compra» que
anuncia la página; `list_promotions` solo mostró AMOR2026 y el bot le dijo
que no había descuento para sus piezas. Un colega tuvo que intervenir.

Contrato:
  * `list_promotions` dice qué cupones son solo para la primera compra y le
    dice al bot que lo aplique cuando el cliente lo pide por su nombre o por
    su condición (el cliente no sabe el código).
  * `apply_coupon` lo aplica a quien no ha comprado y lo rechaza, con la razón,
    a quien ya compró. Si no se puede confirmar, no lo aplica (falla cerrada).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from exoclaw.agent.tools import ToolContext

from src.platform.promotions.port import FakePromotionsPort
from src.platform.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.tools.coupons import ApplyCouponTool, ListPromotionsTool
from src.plugins.chats.agent.sales.use_cases.coupon_application import resolve_coupon_application
from src.sdk.connectorkit import InMemoryOrderFacts, OrderFacts
from tests.plugins.chats.sales.test_coupon_tools import KEY, FakeCatalog, _md, _promo, _seed

_WELCOME = _promo(id="promo_bienvenida", code="BIENVENIDA", value=5, description="Descuento de bienvenida",
                  first_purchase_only=True)
_AMOR = _promo(id="promo_amor", code="AMOR2026", value=10, product_ids=("prod_cubo-love",),
               description="Amor y amistad 2026")


def _returning() -> dict:
    """Una compra anterior (episodio cerrado) y el episodio de hoy abierto."""
    return {
        "episodes": [
            {"episode_id": "ep_001", "started_at_ms": 1, "closed_at_ms": 2, "closing_tag": "COMPRA_EXITOSA",
             "order_id": "order_01OLD"},
            {"episode_id": "ep_002", "started_at_ms": 3, "closed_at_ms": None},
        ],
        "registered_orders_history": [{"order_id": "order_01OLD", "provider": "medusa", "success": True, "ts_ms": 2}],
    }


def _delivered() -> InMemoryOrderFacts:
    return InMemoryOrderFacts([
        OrderFacts(order_id="order_01OLD", display_id="31", total_cop=45000, currency_code="cop", pay_status="paid",
                   stage="delivered", customer="Cliente", is_draft=False)
    ])


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(session_key=KEY, channel="whatsapp", chat_id=KEY)


def _apply(vault: Path, facts) -> ApplyCouponTool:
    return ApplyCouponTool(
        workspace=str(vault), metadata_store=FilesystemMetadataStore(vault),
        promotions=FakePromotionsPort([_WELCOME, _AMOR]), catalog=FakeCatalog(), order_facts=facts,
    )


def _list(vault: Path, facts) -> ListPromotionsTool:
    return ListPromotionsTool(
        workspace=str(vault), promotions=FakePromotionsPort([_WELCOME, _AMOR]), catalog=FakeCatalog(),
        metadata_store=FilesystemMetadataStore(vault), order_facts=facts,
    )


@pytest.mark.asyncio
async def test_a_first_time_customer_gets_the_welcome_coupon(ctx, _isolate_vault_dir):
    path = _seed(_isolate_vault_dir)

    env = json.loads(await _apply(_isolate_vault_dir, _delivered()).execute_with_context(ctx, code="bienvenida"))

    assert env["applied"] is True
    assert _md(path)["episodes"][-1]["applied_coupon"]["code"] == "BIENVENIDA"


@pytest.mark.asyncio
async def test_a_customer_who_already_bought_is_told_why_not(ctx, _isolate_vault_dir):
    path = _seed(_isolate_vault_dir, _returning())

    env = json.loads(await _apply(_isolate_vault_dir, _delivered()).execute_with_context(ctx, code="BIENVENIDA"))

    assert env["applied"] is False
    assert env["reason"] == "first_purchase_only"
    assert "primera compra" in env["summary"]
    assert "applied_coupon" not in _md(path)["episodes"][-1]


@pytest.mark.asyncio
async def test_when_the_purchases_cannot_be_checked_the_coupon_is_not_applied(ctx, _isolate_vault_dir):
    path = _seed(_isolate_vault_dir, _returning())

    env = json.loads(
        await _apply(_isolate_vault_dir, InMemoryOrderFacts(available=False)).execute_with_context(ctx, code="BIENVENIDA")
    )

    assert env["applied"] is False
    assert env["reason"] == "first_purchase_unknown"
    assert "applied_coupon" not in _md(path)["episodes"][-1]


@pytest.mark.asyncio
async def test_list_promotions_tells_the_bot_which_coupon_is_the_welcome_one(ctx, _isolate_vault_dir):
    _seed(_isolate_vault_dir)

    env = json.loads(await _list(_isolate_vault_dir, _delivered()).execute_with_context(ctx))

    welcome = next(p for p in env["promotions"] if p["code"] == "BIENVENIDA")
    assert welcome["first_purchase_only"] is True
    assert welcome["name"] == "Descuento de bienvenida"
    assert "BIENVENIDA" in env["summary"] and "primera compra" in env["summary"]
    assert "Descuento de bienvenida" in env["summary"]
    # El cliente no sabe el código: lo pide por su nombre o su condición.
    assert "por su nombre" in env["summary"]


@pytest.mark.asyncio
async def test_list_promotions_does_not_offer_the_welcome_coupon_to_a_returning_customer(ctx, _isolate_vault_dir):
    _seed(_isolate_vault_dir, _returning())

    env = json.loads(await _list(_isolate_vault_dir, _delivered()).execute_with_context(ctx))

    welcome = next(p for p in env["promotions"] if p["code"] == "BIENVENIDA")
    assert welcome["applies_to_customer"] is False
    assert "ya compró" in env["summary"]


@pytest.mark.asyncio
async def test_a_door_that_cannot_check_purchases_does_not_apply_it() -> None:
    # Cualquier camino nuevo que valide cupones sin mirar las compras falla
    # cerrado con el de bienvenida (L-32: el cupón llega por todas las puertas).
    application = await resolve_coupon_application(
        "BIENVENIDA", promotions=FakePromotionsPort([_WELCOME]), quotas=None, sales=None, catalog=FakeCatalog(),
        now_ms=1,
    )

    assert application.applied is False
    assert application.reason == "first_purchase_unknown"


def test_the_condition_travels_in_the_snapshot_of_the_applied_coupon() -> None:
    from dataclasses import asdict

    from src.plugins.chats.agent.sales.use_cases.coupons import promotion_from_snapshot

    assert promotion_from_snapshot(asdict(_WELCOME)).first_purchase_only is True
    # Un cupón aplicado antes de que existiera el campo no es de primera compra.
    old = {k: v for k, v in asdict(_AMOR).items() if k != "first_purchase_only"}
    assert promotion_from_snapshot(old).first_purchase_only is False


def test_a_campaign_with_the_welcome_coupon_leaves_it_to_apply_coupon() -> None:
    # El webhook no mira las compras: no lo aplica solo y le pide al bot que
    # lo aplique con `apply_coupon`, que sí las mira.
    from src.plugins.chats.agent.sales.use_cases.campaign_reply import build_campaign_reply_note
    from src.plugins.chats.agent.sales.use_cases.coupon_application import CouponApplication

    note = build_campaign_reply_note(
        {"campaign_name": "Bienvenida", "coupon_code": "BIENVENIDA"},
        coupon=CouponApplication("BIENVENIDA", "first_purchase_unknown", _WELCOME),
    )

    assert "apply_coupon(code='BIENVENIDA')" in note
