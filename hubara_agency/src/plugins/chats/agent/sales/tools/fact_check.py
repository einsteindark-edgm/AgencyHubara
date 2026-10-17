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
import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from src.plugins.chats.agent.sales.catalog_scope import WHOLE_CATALOG
from src.plugins.chats.agent.sales.decisions.guards import (
    bulk_reading,
    check_facts,
    fact_words,
    facts_in_bundle,
    promised_handoff,
)
from src.plugins.chats.agent.sales.state import FilesystemMetadataStore
from src.plugins.chats.agent.sales.use_cases.order_draft import get_active_draft
from src.plugins.chats.agent.sales.use_cases.pack_quantity import BULK_UNITS, candle_counts, loose_candles_note
from src.plugins.chats.agent.sales.use_cases.product_options import (
    aromas_of,
    boxed_products,
    catalog_labels,
    colors_of,
    focus_products,
    option_sheets,
    pack_units,
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
from src.plugins.chats.agent.sales.price_quotes import extract_cop_amounts
from src.plugins.chats.agent.sales.pricing import accepted_prices, catalog_prices, catalog_unit_price, format_cop
from src.plugins.chats.agent.sales.use_cases.coupons import applied_coupon, describe_promotion
from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import get_active_episode
from src.plugins.chats.agent.sales.use_cases.fact_rules import (
    CouponWords,
    StatusWords,
    talks_about_coupons,
    talks_about_order,
)
from src.plugins.chats.shared.draft_items import draft_items, find_product
from src.plugins.chats.shared.store_pack import store_domain, vocabulary
from src.sdk.connectorkit import coupon_problem
from src.plugins.chats.agent.sales.use_cases.reply_facts import ReplyFacts, discounted, fact_message

#: La marca de la retención, por mensaje del cliente, para `send_reply` y las
#: tarjetas juntas: lo que ya se le dijo al modelo (`terms`) no se retiene otra
#: vez y, como mucho, se retiene `MAX_HOLDS_PER_MESSAGE` veces (nunca un bucle).
FACT_CHECK_KEY = "fact_check_checked"
FACT_CHECK_REPORTED_KEY = "fact_check_reported"
MAX_HOLDS_PER_MESSAGE = 2
FACT_CHECK_ERROR = "fact_check"
#: Los montos de política que un texto puede decir con su contexto («contra
#: entrega desde $45.000», «el envío nacional cuesta $16.940»): los mismos
#: que acepta la verificación del checkout.
POLICY_AMOUNTS = (CASH_ON_DELIVERY_MIN_PRODUCTS_COP, SHIPPING_RATE_BOGOTA_COP, SHIPPING_RATE_NATIONAL_COP)


@dataclass(frozen=True)
class _PromisedBulk:
    """La retención del texto que promete un colega por unas velas sueltas
    (`por_mayor`), con la forma de los hallazgos de los hechos."""

    terms: tuple[str, ...] = ("por_mayor",)
    checks: tuple[str, ...] = ("por_mayor",)


#: Los mensajes del cliente que se leen de su ráfaga (los últimos) y los
#: eventos de lo último de la charla que dicen de qué productos se habla.
MAX_BURST_MESSAGES = 6
MAX_TALK_EVENTS = 8
#: Sin pedido anotado, los productos de la charla cuyas sumas (uno de cada uno) son ciertas.
MAX_TALKED_PRODUCTS = 6

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
        history_reader: Callable[[str], list[dict[str, Any]]] | None = None,
    ) -> None:
        """`promotions` (PromotionsPort) y `order_facts` (OrderFacts): las
        fuentes de los cupones y del pedido; sin ellas, esos hechos no se
        revisan. `now_ms`: el reloj con que se decide qué cupones rigen.
        `history_reader` (`composition.build_session_history_reader`): el
        historial del panel, de donde sale lo que acaba de escribir el
        cliente; sin él, sus cifras no se revisan."""
        self._vault_dir = Path(vault_dir) if vault_dir is not None else None
        self._catalog = catalog
        self._promotions = promotions
        self._order_facts = order_facts
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._history_reader = history_reader

    async def _order(self, metadata: dict[str, Any], text: str) -> tuple[str | None, str | None, str | None, int | None]:
        """(número, etapa, pago, total) del pedido del episodio según
        OrderFacts; solo si el texto habla del pago o del envío con las frases
        de `estado_ajeno`, o dice un monto (el total real del pedido, con el
        envío que cobró la tienda, también es un precio que vale). Sin eso no
        se consulta."""
        order_id = (get_active_episode(metadata) or {}).get("order_id")
        if self._order_facts is None or not order_id or not (
            extract_cop_amounts(text) or _talks("estado_ajeno", StatusWords, talks_about_order, text)
        ):
            return None, None, None, None
        try:
            # Fresco: el caché del worker vive 60 s y solo lo invalida el bus de
            # la API (tras «Confirmar pago» seguía «pendiente», revisión 2026-10-13).
            invalidate = getattr(self._order_facts, "invalidate", None)
            if callable(invalidate):
                invalidate(order_id)
            snapshot = await asyncio.wait_for(self._order_facts.get_facts([order_id]), timeout=SOURCE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — Medusa lento o caído: el estado no se revisa
            logger.warning("💬 [fact_check] OrderFacts no respondió ({!r})", exc)
            return None, None, None, None
        fact = snapshot.facts.get(order_id)
        # Un valor viejo (Medusa no respondió) o sin confirmar no se juzga.
        if fact is None or getattr(snapshot, "stale", False) or order_id in getattr(snapshot, "unresolved", ()):
            return None, None, None, None
        total = getattr(fact, "total_cop", None)
        return (
            f"#{fact.display_id}" if fact.display_id else None, fact.stage, fact.pay_status,
            int(total) if isinstance(total, int | float) and total > 0 else None,
        )

    async def _coupons(self, metadata: dict[str, Any], text: str) -> tuple[dict[str, Any], list[Any]]:
        """Los cupones que Medusa conoce y los vigentes (los campos de
        `ReplyFacts` y las promociones vigentes); solo si el texto habla de
        cupones o descuentos con las frases de `cupon_ajeno`, o escribe algo
        como un código (sin eso no se consulta Medusa). Sin respuesta, no se
        sabe: `coupons_checked` queda en falso y no se juzga ningún cupón."""
        if self._promotions is None or not _talks("cupon_ajeno", CouponWords, talks_about_coupons, text):
            return {}, []
        try:
            promotions = await asyncio.wait_for(self._promotions.list_active(), timeout=SOURCE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — Medusa lento o caído: los cupones no se revisan
            logger.warning("💬 [fact_check] cupones no disponibles ({!r})", exc)
            return {}, []
        now_ms = self._now_ms()
        valid = [p for p in promotions if coupon_problem(p, now_ms=now_ms) is None]
        applied = (applied_coupon(metadata) or {}).get("code")
        return {
            "coupons_checked": True,
            "coupons_known": tuple(p.code for p in promotions),
            "coupons_valid": tuple(dict.fromkeys([*(p.code for p in valid), *([applied] if applied else [])])),
            "coupons_lines": tuple(f"{p.code}: {describe_promotion(p)}" for p in valid),
            "coupons_percents": tuple(p.value for p in valid if p.discount_type == "percentage"),
        }, valid

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
        (order, stage, pay_status, total), (coupons, valid_promotions) = await asyncio.gather(
            self._order(metadata, text), self._coupons(metadata, text)
        )
        base: set[int] = set()
        for product in focus:
            base |= set(catalog_prices(product))
            base |= accepted_prices(self._vault_dir, session_key, product.handle, snapshot_price_cop=None)
        prices = set(base)
        # Con el cupón aplicado y con cualquier cupón vigente que el texto le
        # ofrezca («con BIENVENIDA te quedan en $23.750»).
        prices |= discounted(base, ((applied_coupon(metadata) or {}).get("promotion")))
        for promotion in valid_promotions:
            prices |= discounted(base, {"discount_type": promotion.discount_type, "value": promotion.value})
        # El total real del pedido (OrderFacts): el envío que cobró la tienda.
        if total is not None:
            prices.add(total)
        subtotal, breakdown = _subtotal(products, metadata)
        burst, talk = self._conversation(session_key)
        figures, truth = _figure_facts(
            products, metadata, subtotal=subtotal, breakdown=breakdown, total=total, talk=f"{talk}\n{text}",
            accepted=lambda handle: accepted_prices(self._vault_dir, session_key, handle, snapshot_price_cop=None),
        )
        own = [*(a for p in focus for a in aromas_of(p)), *(c for p in focus for c in colors_of(p)),
               *(v for p in focus for v in presentations_of(p))]
        sold_by_box = store_domain().get("sold_by_box")
        boxes = tuple(
            (p.title, units, catalog_unit_price(p)) for p in focus if (units := pack_units(p, sold_by_box))
        )
        return ReplyFacts(
            focus=tuple(focus), aromas=tuple(aromas), colors=tuple(colors), sheets=option_sheets(focus),
            single=single_sheets(focus),
            prices=tuple(sorted(prices)), policy=POLICY_AMOUNTS,
            subtotal=subtotal, order_breakdown=breakdown, cod_min=CASH_ON_DELIVERY_MIN_PRODUCTS_COP,
            surcharges=(PAYMENT_LINK_SURCHARGE_NEQUI_BANCOLOMBIA, PAYMENT_LINK_SURCHARGE_OTHER_BANKS),
            order=order, stage=stage, pay_status=pay_status,
            titles_all=tuple(p.title for p in products if getattr(p, "title", None)),
            own_labels=tuple(dict.fromkeys(own)),
            names=_customer_names(metadata),
            customer_text=burst, order_figures=figures, figure_truth=truth, boxes=boxes,
            **coupons,
        )

    def _conversation(self, session_key: str) -> tuple[str, str]:
        """(lo que el cliente escribió desde la última respuesta de la tienda,
        lo último de la charla). La ráfaga es la de este turno: el ingest la
        guarda en el historial antes del turno. Lo último de la charla (textos
        y títulos de las tarjetas) dice de qué productos se habla cuando
        todavía no hay pedido anotado. Vacíos sin lector o si falla (las
        cifras del cliente no se revisan)."""
        if self._history_reader is None:
            return "", ""
        try:
            events = [e for e in (self._history_reader(session_key) or []) if isinstance(e, dict)]
        except Exception as exc:  # noqa: BLE001 — sin historial: sus cifras no se revisan
            logger.warning("💬 [fact_check] historial no disponible ({!r}) session={}", exc, session_key)
            return "", ""
        burst: list[str] = []
        for event in reversed(events):
            if event.get("role") != "user":
                break
            burst.append(str(event.get("content") or ""))
        talk: list[str] = []
        for event in events[-MAX_TALK_EVENTS:]:
            card = event.get("card") if isinstance(event.get("card"), dict) else {}
            titles = [str(i.get("title")) for i in card.get("items") or () if isinstance(i, dict) and i.get("title")]
            talk.append(" ".join([str(event.get("content") or ""), *titles]))
        return "\n".join(reversed(burst[:MAX_BURST_MESSAGES])), "\n".join(talk)

    async def held(
        self, session_key: str, text: str, *, tool: str = "send_reply", handles: Sequence[str] | None = None,
        catalog: CatalogRead | None = None,
    ) -> str | None:
        """El envelope que devuelve `text` al modelo, o None (sale). `catalog`:
        la lectura del catálogo que comparte quien llama (si no, una propia).

        Por mensaje del cliente: lo que ya se le dijo al modelo no se retiene
        otra vez (insiste con lo mismo: sale); algo NUEVO sí (premortem del PR
        #426: una retención en una tarjeta apagaba la revisión del error real
        que venía después), y como mucho `MAX_HOLDS_PER_MESSAGE` veces."""
        if self._catalog is None or self._vault_dir is None or not (text or "").strip():
            return None
        try:
            if not facts_in_bundle():
                return None
            holds, reported = _reported(FilesystemMetadataStore(self._vault_dir).read(session_key) or {})
            if holds >= MAX_HOLDS_PER_MESSAGE:
                return None
            facts = await self.facts(session_key, text, handles=handles, catalog=catalog)
            findings: Any = await check_facts(text, facts, session_id=session_key, vault_dir=self._vault_dir)
            if findings and not (holds and set(findings.terms) <= reported):
                store = vocabulary()
                message = fact_message(
                    findings, facts, tool=tool, tone=store.get("correction_tone"),
                    price_examples=list(store.get("correction_examples") or ()),
                    sum_examples=list(store.get("sum_correction_examples") or ()),
                )
            else:
                promised = await self._promised_bulk(session_key, text, tool=tool)
                if promised is None or (holds and {"por_mayor"} <= reported):
                    return None
                findings, message = _PromisedBulk(), promised
        except Exception as exc:  # noqa: BLE001 — nunca frena la respuesta
            logger.warning("💬 [TOOL {}] sin revisar los hechos ({!r}) session={}", tool, exc, session_key)
            return None
        return self._hold(session_key, findings, message, tool=tool)

    async def _promised_bulk(self, session_key: str, text: str, *, tool: str) -> str | None:
        """El mensaje con que vuelve un texto que le promete un colega al
        cliente por la cantidad cuando eran velas sueltas de lo que se cobra
        por caja; None si no. Laboratorio A/B del 2026-10-15 (ventas-7, capas
        como en producción): «quiero 30 velitas de las onduladas» → «son 3
        cajas. Para esa cantidad te coordino con un colega», sin
        escalate_to_human, y la red del relevo prometido lo pasó al equipo.
        Solo con una cifra del cliente que en velas cabe en 20 cajas o menos
        (`candle_counts`, sin llamar a nadie), un texto que promete el relevo
        (`relevo`) y la lectura de `por_mayor` («velas» o la duda)."""
        boxes = boxed_products(store_domain().get("sold_by_box"))
        if not boxes:
            return None
        burst, talk = self._conversation(session_key)
        cifras = candle_counts(burst, [units for _name, units in boxes])
        if not cifras or not await promised_handoff(text, session_id=session_key, vault_dir=self._vault_dir):
            return None
        reading = await bulk_reading(
            f"[cliente] {burst.strip()}\n[asesor, va a responder] {text.strip()[:400]}",
            cifras, [name for name, _u in boxes], [units for _n, units in boxes],
            session_id=session_key, vault_dir=self._vault_dir,
        )
        if reading is None or reading == "por_mayor":
            return None
        return (
            "No se envió: le prometes pasarlo a un colega por la cantidad, pero no es un pedido al por mayor. "
            f"{loose_candles_note(boxes, cifras)} No le prometas un colega: anota las cajas con set_order_slot y "
            f"sigue la venta. Si de verdad pide más de {BULK_UNITS} cajas u otro producto, llama "
            f"escalate_to_human; si no aplica, vuelve a llamar {tool} con el mismo texto."
        )

    def _hold(self, session_key: str, findings: Any, message: str, *, tool: str) -> str | None:
        """Anota la retención (con lo que se le dijo) y devuelve el envelope;
        None si otra tool ya retuvo lo mismo o no se puede anotar (sale)."""
        store = FilesystemMetadataStore(self._vault_dir)
        terms = set(findings.terms)
        wrote: list[bool] = []

        def _mark(fresh: dict[str, Any]) -> dict[str, Any] | None:
            inbound = fresh.get("last_inbound_message_id") if fresh else None
            if not inbound:
                return None
            holds, reported = _reported(fresh)
            if holds >= MAX_HOLDS_PER_MESSAGE or (holds and terms <= reported):
                return None
            fresh[FACT_CHECK_KEY] = inbound
            fresh[FACT_CHECK_REPORTED_KEY] = {
                "inbound": inbound, "holds": holds + 1, "terms": sorted(reported | terms),
            }
            wrote.append(True)
            return fresh

        try:
            store.update(session_key, _mark)
        except Exception:  # noqa: BLE001 — sin la marca podría repetirse: el texto sale
            return None
        if not wrote:
            return None
        checks = list(findings.checks)
        logger.info("💬 [TOOL {}] retenido ({}: {}) session={}", tool, FACT_CHECK_ERROR, ", ".join(checks), session_key)
        status_key = "sent" if tool == "send_reply" else "queued"
        return json.dumps(
            {status_key: False, "error": FACT_CHECK_ERROR, "checks": checks, "message": message}, ensure_ascii=False
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


def _reported(metadata: dict[str, Any]) -> tuple[int, set[str]]:
    """(retenciones, lo que ya se le dijo al modelo) en ESTE mensaje del cliente."""
    inbound = metadata.get("last_inbound_message_id")
    mark = metadata.get(FACT_CHECK_REPORTED_KEY)
    if not inbound or not isinstance(mark, dict) or mark.get("inbound") != inbound:
        return 0, set()
    holds = mark.get("holds")
    return (holds if isinstance(holds, int) else 0), {str(t) for t in mark.get("terms") or ()}


def _customer_names(metadata: dict[str, Any]) -> tuple[str, ...]:
    """Las palabras del nombre del cliente: su perfil de WhatsApp y quien
    recibe el pedido (premortem del PR #426: «Gracias Lila» se retenía por el
    color Lila)."""
    profile = metadata.get("profile") if isinstance(metadata.get("profile"), dict) else {}
    draft = get_active_draft(metadata) or {}
    slots = draft.get("slots") if isinstance(draft.get("slots"), dict) else {}
    raw = [profile.get("name"), slots.get("nombre_recibe")]
    return tuple(dict.fromkeys(w for r in raw if isinstance(r, str) for w in re.findall(r"[^\W\d_]{3,}", r)))


def _figure_facts(
    products: list[Any], metadata: dict[str, Any], *, subtotal: int | None, breakdown: str | None,
    total: int | None, talk: str, accepted: Callable[[str], set[int]],
) -> tuple[tuple[int, ...], str | None]:
    """(las cifras ciertas, lo cierto en palabras) contra lo que se mira la
    cifra que afirma el cliente (`cifra_del_cliente`). Con pedido anotado, lo
    que suma. Sin pedido anotado (laboratorio 2026-10-14: el bot casi nunca lo
    anotaba antes de la cuenta del cliente), los precios del catálogo de los
    productos de la charla (`talk`) y sus sumas con uno de cada uno («me da
    $47.500» por una cuadrada y una ondulada es cierto; «$45.000», no): la
    cuenta no está hecha y el modelo la hace. Con un pedido registrado sin
    borrador, o sin un precio, no se sabe (None): no se juzga."""
    episode = get_active_episode(metadata) or {}
    if draft_items(get_active_draft(metadata)):
        promotion = (applied_coupon(metadata) or {}).get("promotion")
        figures = _order_figures(products, metadata, total=total, promotion=promotion, accepted=accepted)
        if not figures or subtotal is None or not breakdown:
            return (), None
        return figures, f"su pedido ({breakdown}) suma {format_cop(subtotal)} en productos"
    if episode.get("order_id"):
        return (), None
    talked = [(p, sorted({*catalog_prices(p), *accepted(p.handle)})) for p in focus_products(products, draft=None, text=talk)]
    priced = [(p, prices) for p, prices in talked if prices][:MAX_TALKED_PRODUCTS]
    sums = {0}
    for _product, prices in priced:
        sums |= {s + price for s in sums for price in prices}
    rates = (SHIPPING_RATE_BOGOTA_COP, SHIPPING_RATE_NATIONAL_COP)
    figures = (sums - {0}) | set(rates) | {s + rate for s in sums - {0} for rate in rates}
    lines = "; ".join(f"{p.title} cuesta {' o '.join(format_cop(x) for x in prices)}" for p, prices in priced)
    truth = "todavía no hay un pedido anotado" + (f"; en el catálogo: {lines}" if lines else "")
    # Con dos o tres productos de un precio, la suma de uno de cada uno es la
    # pista que le sirve a Jev (sonda 2026-10-14: los avisos falsos bajan).
    if 2 <= len(priced) <= 3 and all(len(prices) == 1 for _p, prices in priced):
        truth += f" (una de cada una suma {format_cop(sum(prices[0] for _p, prices in priced))})"
    return tuple(sorted(figures)), truth


def _order_figures(
    products: list[Any], metadata: dict[str, Any], *, total: int | None, promotion: dict[str, Any] | None,
    accepted: Callable[[str], set[int]],
) -> tuple[int, ...]:
    """Las cifras ciertas del pedido del borrador: el precio de cada producto
    (catálogo y en vivo), cada línea por su cantidad, lo que suman (con cada
    precio posible), las tarifas de envío y la suma con cada una, con el
    descuento aplicado, y el total registrado (OrderFacts). Vacío si falta un
    producto, un precio o una cantidad (sin eso no se juzga la cifra del
    cliente). El mínimo de contra entrega NO: «1 cuadrada + 1 ondulada me da
    $45.000» es justo esa cifra."""
    items = draft_items(get_active_draft(metadata))
    if not items:
        return ()
    singles: set[int] = set()
    sums = {0}
    for item in items:
        product = find_product(products, item.get("producto"))
        raw = str(item.get("cantidad") or "").strip()
        prices = {*catalog_prices(product), *accepted(product.handle)} if product is not None else set()
        if not prices or not raw.isdigit() or int(raw) <= 0:
            return ()
        quantity = int(raw)
        lines = {price * quantity for price in prices}
        singles |= prices | lines
        sums = {s + line for s in sums for line in lines}
    rates = (SHIPPING_RATE_BOGOTA_COP, SHIPPING_RATE_NATIONAL_COP)
    out = singles | sums | set(rates) | {s + rate for s in sums for rate in rates}
    out |= discounted(sums, promotion)
    if total is not None:
        out.add(total)
    return tuple(sorted(out))


def _subtotal(products: list[Any], metadata: dict[str, Any]) -> tuple[int | None, str | None]:
    """Los productos del borrador × su precio del catálogo y cómo se arma
    («2 × $25.000»); (None, None) si falta un precio, una cantidad o no hay
    productos (sin dato, el pago no se revisa). Laboratorio 2026-10-14: sin la
    cantidad contaba 1 caja y el bot «corrigió» el contra entrega."""
    total = 0
    parts: list[str] = []
    items = draft_items(get_active_draft(metadata))
    for item in items:
        price = catalog_unit_price(find_product(products, item.get("producto")))
        raw = str(item.get("cantidad") or "").strip()
        if price is None or not raw.isdigit():
            return None, None
        quantity = int(raw)
        if quantity <= 0:
            return None, None
        total += price * quantity
        parts.append(f"{quantity} × {format_cop(price)}")
    return (total, " + ".join(parts)) if items else (None, None)


__all__ = ["FACT_CHECK_ERROR", "FACT_CHECK_KEY", "CatalogRead", "FactCheck", "held_text"]
