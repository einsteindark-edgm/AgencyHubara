"""Los hechos antes de enviar: lo que el bot le va a decir al cliente contra lo
que el sistema sabe (incidente 2026-10-13).

`send_reply` y las tools de tarjetas (el texto de la lista, del selector, de la
ficha, de los botones) le piden a `FactCheck.held` que revise su texto. El
código arma los hechos UNA vez —el catálogo, de qué producto habla el texto,
el pedido— y las capacidades del paquete deciden qué no coincide
(`guards.check_facts`): la regla propone y Jev solo juzga lo semántico sobre
la comparación ya armada. Lo que no coincide vuelve al modelo en UNA retención
por mensaje del cliente, con lo cierto y la salida (el mismo texto si el aviso
no aplica). Sin catálogo, sin vault o si algo falla: el texto sale.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from loguru import logger

from src.plugins.chats.agent.sales.catalog_scope import WHOLE_CATALOG
from src.plugins.chats.agent.sales.decisions.guards import check_facts, fact_words, facts_in_bundle
from src.plugins.chats.agent.sales.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.tools.retention import held_before, hold_once
from src.plugins.chats.agent.sales.use_cases.order_draft import get_active_draft
from src.plugins.chats.agent.sales.use_cases.product_options import (
    aromas_of,
    catalog_labels,
    colors_of,
    focus_products,
    option_sheets,
    presentations_of,
    single_sheets,
)
from src.plugins.chats.agent.sales.config.shipping import (
    CASH_ON_DELIVERY_MIN_PRODUCTS_COP,
    SHIPPING_RATE_BOGOTA_COP,
    SHIPPING_RATE_NATIONAL_COP,
)
from src.plugins.chats.agent.sales.config.payments import (
    PAYMENT_LINK_SURCHARGE_NEQUI_BANCOLOMBIA,
    PAYMENT_LINK_SURCHARGE_OTHER_BANKS,
)
from src.plugins.chats.agent.sales.pricing import accepted_prices, catalog_prices, catalog_unit_price
from src.plugins.chats.agent.sales.use_cases.coupons import applied_coupon, describe_promotion
from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import get_active_episode
from src.plugins.chats.agent.sales.use_cases.fact_rules import (
    CouponWords,
    StatusWords,
    talks_about_coupons,
    talks_about_order,
)
from src.plugins.chats.shared.draft_items import draft_items, find_product
from src.sdk.connectorkit import coupon_problem
from src.plugins.chats.agent.sales.use_cases.reply_facts import ReplyFacts, discounted, fact_message

#: La marca de la retención: una por mensaje del cliente para `send_reply` y
#: las tarjetas juntas (el modelo corrige una vez, nunca un bucle).
FACT_CHECK_KEY = "fact_check_checked"
FACT_CHECK_ERROR = "fact_check"
#: Los montos de política que un texto puede decir con su contexto («contra
#: entrega desde $45.000», «el envío nacional cuesta $16.940»): los mismos
#: que acepta la verificación del checkout.
POLICY_AMOUNTS = (CASH_ON_DELIVERY_MIN_PRODUCTS_COP, SHIPPING_RATE_BOGOTA_COP, SHIPPING_RATE_NATIONAL_COP)


#: Lo que se espera a Medusa por el pedido o los cupones: si no responde, ese
#: hecho no se revisa (el texto nunca espera más que esto por una revisión).
SOURCE_TIMEOUT_S = 3.0


class CatalogRead:
    """El catálogo entero leído a lo sumo UNA vez por texto: `send_reply` lo
    comparte entre la revisión de los hechos y la de la lista de opciones (antes
    eran dos lecturas por mensaje). Si la lectura falla, falla igual para las
    dos: no se reintenta."""

    def __init__(self, catalog: Any) -> None:
        self._catalog = catalog
        self._products: list[Any] | None = None
        self._error: Exception | None = None

    async def products(self) -> list[Any]:
        if self._error is not None:
            raise self._error
        if self._products is None:
            try:
                self._products = list((await self._catalog.search(q="", limit=WHOLE_CATALOG)).results)
            except Exception as exc:
                self._error = exc
                raise
        return self._products


def _talks(name: str, words_type: type, talks: Callable[..., bool], text: str) -> bool:
    """¿El texto habla de lo que revisa `name`, según las frases del paquete?
    Sin la decisión en el paquete, no; con una frase mal escrita, tampoco (la
    decisión misma tampoco correría: se registra y el texto sale)."""
    words = fact_words(name)
    if words is None:
        return False
    try:
        return talks(text, words_type(**words))
    except (TypeError, ValueError) as exc:
        logger.error("💬 [fact_check] las frases de {} no se pueden leer ({})", name, exc)
        return False


class FactCheck:
    def __init__(
        self,
        vault_dir: str | Path | None,
        catalog: Any,
        *,
        promotions: Any = None,
        order_facts: Any = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        """`promotions` (PromotionsPort) y `order_facts` (OrderFacts): las
        fuentes de los cupones y del pedido; sin ellas, esos hechos no se
        revisan. `now_ms`: el reloj con que se decide qué cupones rigen."""
        self._vault_dir = Path(vault_dir) if vault_dir is not None else None
        self._catalog = catalog
        self._promotions = promotions
        self._order_facts = order_facts
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))

    async def _order(self, metadata: dict[str, Any], text: str) -> tuple[str | None, str | None, str | None]:
        """(número, etapa, pago) del pedido del episodio según OrderFacts; solo
        si el texto habla del pago o del envío con las frases de `estado_ajeno`
        (sin eso no se consulta)."""
        order_id = (get_active_episode(metadata) or {}).get("order_id")
        if self._order_facts is None or not order_id or not _talks("estado_ajeno", StatusWords, talks_about_order, text):
            return None, None, None
        try:
            # Fresco: el caché del worker vive 60 s y solo lo invalida el bus de
            # la API (tras «Confirmar pago» seguía «pendiente», revisión 2026-10-13).
            invalidate = getattr(self._order_facts, "invalidate", None)
            if callable(invalidate):
                invalidate(order_id)
            snapshot = await asyncio.wait_for(self._order_facts.get_facts([order_id]), timeout=SOURCE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — Medusa lento o caído: el estado no se revisa
            logger.warning("💬 [fact_check] OrderFacts no respondió ({})", exc)
            return None, None, None
        fact = snapshot.facts.get(order_id)
        # Un valor viejo (Medusa no respondió) o sin confirmar no se juzga.
        if fact is None or getattr(snapshot, "stale", False) or order_id in getattr(snapshot, "unresolved", ()):
            return None, None, None
        return f"#{fact.display_id}" if fact.display_id else None, fact.stage, fact.pay_status

    async def _coupons(self, metadata: dict[str, Any], text: str) -> dict[str, tuple[Any, ...]]:
        """Los cupones que Medusa conoce y los vigentes; solo si el texto habla
        de cupones o descuentos con las frases de `cupon_ajeno`, o escribe algo
        como un código (sin eso no se consulta Medusa)."""
        if self._promotions is None or not _talks("cupon_ajeno", CouponWords, talks_about_coupons, text):
            return {}
        try:
            promotions = await asyncio.wait_for(self._promotions.list_active(), timeout=SOURCE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — Medusa lento o caído: los cupones no se revisan
            logger.warning("💬 [fact_check] cupones no disponibles ({})", exc)
            return {}
        now_ms = self._now_ms()
        valid = [p for p in promotions if coupon_problem(p, now_ms=now_ms) is None]
        applied = (applied_coupon(metadata) or {}).get("code")
        return {
            "coupons_known": tuple(p.code for p in promotions),
            "coupons_valid": tuple(dict.fromkeys([*(p.code for p in valid), *([applied] if applied else [])])),
            "coupons_lines": tuple(f"{p.code}: {describe_promotion(p)}" for p in valid),
            "coupons_percents": tuple(p.value for p in valid if p.discount_type == "percentage"),
        }

    async def facts(
        self, session_key: str, text: str, *, handles: Sequence[str] | None = None, catalog: CatalogRead | None = None
    ) -> ReplyFacts:
        """Lo que el código sabe para revisar `text` (una lectura del catálogo,
        la de `catalog` si quien llama ya la comparte). `handles`: los
        productos de la tarjeta (la ficha, el selector, la lista): esos son el
        foco, no el pedido (revisión 2026-10-13: la ficha de la Plegaria se
        comparaba con las velas de deseos del pedido)."""
        metadata = FilesystemMetadataStore(self._vault_dir).read(session_key) or {}
        products = await (catalog or CatalogRead(self._catalog)).products()
        if handles:
            by_handle = {p.handle: p for p in products}
            focus = [by_handle[h] for h in dict.fromkeys(handles) if h in by_handle]
        else:
            focus = focus_products(products, draft=get_active_draft(metadata), text=text)
        aromas, colors = catalog_labels(products)
        prices: set[int] = set()
        for product in focus:
            prices |= set(catalog_prices(product))
            prices |= accepted_prices(self._vault_dir, session_key, product.handle, snapshot_price_cop=None)
        coupon = applied_coupon(metadata)
        prices |= discounted(prices, (coupon or {}).get("promotion"))
        (order, stage, pay_status), coupons = await asyncio.gather(
            self._order(metadata, text), self._coupons(metadata, text)
        )
        own = [*(a for p in focus for a in aromas_of(p)), *(c for p in focus for c in colors_of(p)),
               *(v for p in focus for v in presentations_of(p))]
        return ReplyFacts(
            focus=tuple(focus), aromas=tuple(aromas), colors=tuple(colors), sheets=option_sheets(focus),
            single=single_sheets(focus),
            prices=tuple(sorted(prices)), policy=POLICY_AMOUNTS,
            subtotal=_subtotal(products, metadata), cod_min=CASH_ON_DELIVERY_MIN_PRODUCTS_COP,
            surcharges=(PAYMENT_LINK_SURCHARGE_NEQUI_BANCOLOMBIA, PAYMENT_LINK_SURCHARGE_OTHER_BANKS),
            order=order, stage=stage, pay_status=pay_status,
            titles_all=tuple(p.title for p in products if getattr(p, "title", None)),
            own_labels=tuple(dict.fromkeys(own)),
            **coupons,
        )

    async def held(
        self, session_key: str, text: str, *, tool: str = "send_reply", handles: Sequence[str] | None = None,
        catalog: CatalogRead | None = None,
    ) -> str | None:
        """El envelope que devuelve `text` al modelo, o None (sale). `catalog`:
        la lectura del catálogo que comparte quien llama (si no, una propia)."""
        if self._catalog is None or self._vault_dir is None or not (text or "").strip() or not facts_in_bundle():
            return None
        if held_before(self._vault_dir, session_key, FACT_CHECK_KEY):
            return None
        try:
            facts = await self.facts(session_key, text, handles=handles, catalog=catalog)
            findings = await check_facts(text, facts, session_id=session_key, vault_dir=self._vault_dir)
        except Exception as exc:  # noqa: BLE001 — nunca frena la respuesta
            logger.warning("💬 [TOOL {}] sin revisar los hechos ({}) session={}", tool, exc, session_key)
            return None
        if not findings:
            return None
        return hold_once(
            self._vault_dir, session_key, FACT_CHECK_KEY, tool=tool, error=FACT_CHECK_ERROR,
            message=fact_message(findings, facts, tool=tool),
            status_key="sent" if tool == "send_reply" else "queued",
        )


async def held_text(
    fact_check: FactCheck | None, ctx: Any, tool: str, text: str | None, *, handles: Sequence[str] | None = None
) -> str | None:
    """Lo que una tarjeta (o `send_reply`) hace con su texto antes de
    encolarse: None = sigue; si no, el envelope con que vuelve al modelo.
    `handles`: los productos de la tarjeta (su foco)."""
    if fact_check is None or not (text or "").strip():
        return None
    return await fact_check.held(ctx.session_key, str(text), tool=tool, handles=handles)


def _subtotal(products: list[Any], metadata: dict[str, Any]) -> int | None:
    """Los productos del borrador × su precio del catálogo; None si falta un
    precio o no hay productos (sin dato, el pago no se revisa)."""
    total = 0
    items = draft_items(get_active_draft(metadata))
    for item in items:
        price = catalog_unit_price(find_product(products, item.get("producto")))
        try:
            quantity = int(str(item.get("cantidad") or 1).strip())
        except ValueError:
            return None
        if price is None or quantity <= 0:
            return None
        total += price * quantity
    return total if items else None


__all__ = ["FACT_CHECK_ERROR", "FACT_CHECK_KEY", "CatalogRead", "FactCheck", "held_text"]
