"""El guion de la campaña para un turno de Ventas (operador, 2026-10-19/20).

Lo arman el ingest (cada mensaje del cliente que va a Ventas) y el turno de
traspaso de remarketing a Ventas. Caso del 2026-10-21 (velitas): el cliente
contestó al toque «No gracias voy a esperar», remarketing le pasó la
conversación a Ventas, y ese turno no traía ni el guion ni la oferta: Ventas se
despidió sin el 5 %. La misma regla en los dos caminos: el guion con los
precios del catálogo y, si el cliente aplazó desde nuestra última respuesta y
la oferta de esta fecha no se le dijo en el episodio, la oferta del cupón ahora.

Muta el metadata (la campaña del episodio, la oferta dicha) y dice si cambió:
quien lo llama lo escribe.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import get_active_episode
from src.plugins.chats.shared.purchase_signals import deferral_since_last_reply

logger = structlog.get_logger()

#: Todo el catálogo (35 productos hoy): los de la campaña siempre entran.
_CATALOG_LIMIT = 200
#: Sin catálogo o sin cupón a tiempo, el guion sale igual (sin precios o sin oferta).
_CATALOG_TIMEOUT_S = 3.0
_COUPON_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class CampaignTurn:
    note: str
    #: Le toca la oferta del cupón en este turno (ya quedó marcada como dicha).
    offer_now: bool
    #: El metadata cambió (la campaña del episodio o la oferta dicha).
    changed: bool


async def campaign_turn(
    metadata: dict[str, Any],
    *,
    catalog: Any,
    now_ms: int,
    text: str | None,
    opening: bool,
    to_sales: bool,
    session_id: str | None = None,
) -> CampaignTurn | None:
    """El guion del episodio activo, o None si no es de ninguna campaña.
    `opening`: el mensaje abrió el episodio (las palabras del anuncio solo
    cuentan ahí). `to_sales`: el turno lo atiende Ventas; la respuesta a un
    toque la atiende remarketing y estas notas no le llegan (se marcaría la
    oferta como dicha sin decirse)."""
    from src.plugins.chats.agent.sales.config.shipping import CASH_ON_DELIVERY_MIN_PRODUCTS_COP
    from src.plugins.chats.agent.sales.use_cases.coupons import applied_coupon
    from src.plugins.chats.shared import sales_campaigns
    from src.plugins.chats.shared.store_pack import store_domain

    episode = get_active_episode(metadata)
    today = datetime.fromtimestamp(now_ms / 1000, tz=ZoneInfo("America/Bogota")).date()
    # Lo que el cliente escribe o muestra ahora (la foto entra como texto con
    # el producto que se reconoció) y la ficha que abrió en este episodio.
    said = " ".join(part for part in (text or "", _card_handle(metadata, episode)) if part)
    campaign = sales_campaigns.campaign_for(episode, today=today, text=(text or "") if opening else "", said=said)
    if campaign is None or episode is None:
        return None
    changed = False
    if episode.get(sales_campaigns.EPISODE_KEY) != campaign.id:
        episode[sales_campaigns.EPISODE_KEY] = campaign.id
        changed = True
        logger.info("sales_campaign_matched", session=session_id, campaign=campaign.id)
    # El catálogo, el cupón y los pedidos anteriores a la vez: con Medusa
    # lento, el turno no suma las esperas (revisión del 2026-10-20). Con un
    # cupón ya aplicado en el episodio no se ofrece otro (el cliente creería
    # que se suman).
    coupon = None if applied_coupon(metadata) else campaign.cupon
    products, promotion, bought = await asyncio.gather(
        _campaign_products(catalog), _campaign_promotion(coupon), _bought_before(metadata) if coupon else _no()
    )
    try:
        boxes = store_domain().get("sold_by_box")
    except Exception:  # noqa: BLE001 — sin el dominio, los precios van por unidad
        boxes = None
    ids = tuple(str(p.id) for p in products if getattr(p, "handle", None) in campaign.productos and getattr(p, "id", None))
    offer = _campaign_offer(promotion, today=today, product_ids=ids, bought_before=bought)
    already = offer is not None and episode.get(sales_campaigns.OFFER_KEY) == offer.key()
    # El cliente aplazó desde nuestra última respuesta (Jev, capacidad
    # `compra`; sin Jev, la regla de hoy), aunque después escribiera otra
    # cosa (caso del 2026-10-21), y la oferta de esta fecha no se le ha dicho
    # en el episodio: es ahora.
    offer_now = bool(offer is not None and to_sales and deferral_since_last_reply(metadata) and not already)
    if offer_now:
        episode[sales_campaigns.OFFER_KEY] = offer.key()
        changed = True
        logger.info("sales_campaign_offer", session=session_id, campaign=campaign.id, offer=offer.key())
    note = sales_campaigns.sales_note(
        campaign, products, cod_min=CASH_ON_DELIVERY_MIN_PRODUCTS_COP, boxes=boxes,
        offer=offer, offer_now=offer_now, already_offered=already,
    )
    return CampaignTurn(note=note, offer_now=offer_now, changed=changed)


def _card_handle(metadata: dict[str, Any], episode: dict[str, Any] | None) -> str:
    """El producto cuya ficha abrió el cliente en este episodio (catálogo de
    WhatsApp o la web, `web_product_ref` ya resuelto), o ''."""
    ref = metadata.get("web_product_ref")
    if not isinstance(ref, dict) or ref.get("status") != "resolved" or episode is None:
        return ""
    if ref.get("episode_id") != episode.get("episode_id"):
        return ""
    return str(ref.get("handle") or "")


async def _campaign_products(catalog: Any) -> list[Any]:
    """El catálogo para los precios del guion; [] si no responde a tiempo."""
    if catalog is None:
        return []
    try:
        result = await asyncio.wait_for(catalog.search("", limit=_CATALOG_LIMIT), timeout=_CATALOG_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — sin precios, el guion igual sale
        logger.warning("sales_campaign_prices_unavailable", reason=type(exc).__name__)
        return []
    return list(result.results)


async def _campaign_promotion(code: str | None) -> Any:
    """La promoción del cupón de la campaña en Medusa; None si no hay o no
    responde a tiempo (el bot no la ofrece)."""
    from src.sdk.connectorkit import get_promotions_port

    if not code:
        return None
    try:
        return await asyncio.wait_for(get_promotions_port().get_by_code(code), timeout=_COUPON_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — sin promociones, no se ofrece el cupón
        logger.warning("sales_campaign_coupon_unavailable", reason=type(exc).__name__)
        return None


def _campaign_offer(promotion: Any, *, today: Any, product_ids: tuple[str, ...], bought_before: bool) -> Any:
    """La oferta si el cupón rige hoy (`coupon_problem`, la vara de
    `list_promotions`: Medusa lo deja «active» al vencer), sus condiciones
    se pueden decir y, si es de primera compra, el cliente no compró antes
    en la conversación (`list_promotions` lo confirma contra Medusa antes
    de decirla). None: el bot no la ofrece."""
    from src.plugins.chats.shared import sales_campaigns
    from src.sdk.connectorkit import coupon_problem

    if promotion is None or coupon_problem(promotion, now_ms=int(time.time() * 1000)) is not None:
        return None
    offer = sales_campaigns.coupon_offer(promotion, today=today, product_ids=product_ids)
    if offer is not None and offer.first_purchase_only and bought_before:
        return None
    return offer


async def _no() -> bool:
    return False


async def _bought_before(metadata: dict[str, Any]) -> bool:
    """¿Un pedido anterior de la conversación es una compra? La regla del
    cupón (`bought_before`, la de `list_promotions`): uno de prueba o
    cancelado no cuenta (caso del 2026-10-21: un número con 7 pedidos, todos
    de prueba, nunca recibía la oferta). Sin pedidos anteriores no pregunta a
    Medusa; si Medusa no responde, cuenta (no se adivina)."""
    from src.plugins.chats.agent.sales.use_cases.first_purchase import (
        FirstPurchaseUnknown,
        bought_before,
        previous_order_ids,
    )

    if not previous_order_ids(metadata):
        return False
    try:
        from src.sdk.connectorkit import get_order_facts_port

        return await bought_before(metadata, get_order_facts_port())
    except FirstPurchaseUnknown:
        return True
    except Exception as exc:  # noqa: BLE001 — sin OrderFacts, como antes: cuenta
        logger.warning("sales_campaign_first_purchase_unknown", reason=type(exc).__name__)
        return True
