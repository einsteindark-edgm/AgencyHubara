"""Cupón aplicado en el episodio — snapshot + descuento recomputado (puro +
una lectura del catálogo).

El cliente da un código → `apply_coupon` lo valida contra Medusa y guarda en
`episodes[-1].applied_coupon` el SNAPSHOT de la promoción. De ahí en adelante
el descuento se RECOMPUTA (confirmación, registro, dashboard) con ese
snapshot y los precios del catálogo: el LLM nunca decide el monto (L-19), y
Medusa puede cambiar a mitad de chat sin que el pedido cambie de precio.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import Any

from src.sdk.connectorkit import (
    QuotaStoreError,
    DiscountLineItem,
    LineDiscount,
    PromotionDTO,
    compute_discount,
)

from src.plugins.chats.agent.sales.use_cases.coupon_quota import (
    ItemVariant,
    combos_by_product_text,
    draft_vs_coupon_lines,
    quota_split,
    resolve_item_variants,
)
from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    ensure_active_episode,
    get_active_episode,
)
from src.plugins.chats.agent.sales.use_cases.order_draft import current_item
from src.plugins.chats.shared.draft_items import draft_items, product_key


@dataclass(frozen=True)
class AppliedDiscount:
    code: str
    discount_cop: int
    applicable_handles: list[str]
    #: None = aplicó; "no_applicable_items" | "min_subtotal" | "unsupported"
    reason: str | None
    min_subtotal_cop: int | None
    description: str | None
    #: Reparto por unidad sobre los ítems (índice = posición en `items`): es
    #: el precio con descuento que el pedido escribe en Medusa.
    line_discounts: tuple[LineDiscount, ...] = ()
    #: True = el cupón tiene cupo por unidad (central de cupones): el reparto
    #: salió de las filas del cupo y lo vendido, no de toda la promoción.
    quota: bool = False
    #: Líneas de un producto con cupo que no dicen el color/aroma que exige.
    missing_attributes: tuple[int, ...] = ()


def applied_coupon(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """`{code, promotion, applied_at_ms}` del episodio activo, o None."""
    episode = get_active_episode(metadata)
    if not episode:
        return None
    raw = episode.get("applied_coupon")
    if not isinstance(raw, dict) or not raw.get("code") or not isinstance(
        raw.get("promotion"), dict
    ):
        return None
    return raw


def promotion_from_snapshot(raw: dict[str, Any]) -> PromotionDTO:
    """Snapshot JSON → DTO (las tuplas viajan como listas en JSON)."""
    data = dict(raw)
    for key in ("product_ids", "variant_ids", "collection_ids", "tag_values"):
        data[key] = tuple(str(x) for x in (data.get(key) or []))
    # Snapshots de antes del campo: sin reglas ilegibles conocidas.
    data["scope_unresolved"] = bool(data.get("scope_unresolved"))
    fields = PromotionDTO.__dataclass_fields__
    return PromotionDTO(**{k: data.get(k) for k in fields})


def is_whole_catalog(promotion: PromotionDTO) -> bool:
    """¿Aplica a todos los productos? Solo si NO filtra productos, no es de
    envío y sus reglas se leyeron completas (falla cerrada)."""
    if promotion.scope_unresolved or promotion.target_type == "shipping_methods":
        return False
    return not (
        promotion.product_ids
        or promotion.variant_ids
        or promotion.collection_ids
        or promotion.tag_values
    )


def _cop_price(variant: Any) -> int | None:
    prices = list(getattr(variant, "prices", None) or [])
    chosen = next((p for p in prices if str(p.currency_code).lower() == "cop"), None)
    if chosen is None:
        return None
    try:
        return int(round(float(chosen.amount)))
    except (TypeError, ValueError):
        return None


async def eligible_products(catalog: Any, promotion: PromotionDTO) -> list[dict[str, Any]]:
    """Productos del catálogo a los que aplica la promo, con su precio y el
    precio con descuento (calculado con `compute_discount`, L-19). Vacío si
    la promo es de todo el catálogo, de envío, o el catálogo no responde."""
    if is_whole_catalog(promotion) or promotion.target_type == "shipping_methods":
        return []
    if catalog is None:
        return []
    try:
        result = await catalog.search("", limit=200)
    except Exception:  # noqa: BLE001 — sin catálogo no hay lista
        return []
    # Precio unitario: el mínimo de compra no cambia el precio de la unidad.
    per_unit = replace(promotion, min_subtotal_cop=None)
    out: list[dict[str, Any]] = []
    # Colecciones: el catálogo no trae el id de colección → como antes, sin
    # lista (el descuento igual lo calcula el cierre con la regla completa).
    selects_by_id = bool(
        promotion.product_ids or promotion.variant_ids or promotion.collection_ids
    )
    for product in getattr(result, "results", None) or []:
        pid = str(getattr(product, "id", "") or "")
        variants = list(getattr(product, "variants", None) or [])
        matching = [v for v in variants if str(getattr(v, "id", "")) in promotion.variant_ids]
        if selects_by_id and pid not in promotion.product_ids and not matching:
            continue
        # Condición por etiquetas (run 28a8e407): Y con la de productos.
        tags = set(getattr(product, "tags", None) or [])
        if promotion.tag_values and not tags & set(promotion.tag_values):
            continue
        variant = (matching or variants or [None])[0]
        price = _cop_price(variant) if variant is not None else None
        if price is None:
            continue
        line = DiscountLineItem(
            handle=str(getattr(product, "handle", "") or ""),
            quantity=1,
            unit_price_cop=price,
            product_id=pid or None,
            variant_id=str(getattr(variant, "id", "")) or None,
            tags=tuple(str(t) for t in tags),
        )
        discount = compute_discount(per_unit, [line]).discount_cop
        out.append(
            {
                "handle": line.handle,
                "title": str(getattr(product, "title", "") or line.handle),
                "price_cop": price,
                "discounted_price_cop": max(price - discount, 0),
            }
        )
    return out


def eligible_products_text(products: list[dict[str, Any]]) -> str:
    """"Cubo Love ($30.000 → $27.000), Vela Buda ($40.000 → $36.000)"."""
    return ", ".join(
        f"{p['title']} ({format_cop(p['price_cop'])} → {format_cop(p['discounted_price_cop'])})"
        for p in products
    )


def set_applied_coupon(
    metadata: dict[str, Any],
    *,
    promotion: PromotionDTO,
    now_ms: int,
    eligible: list[dict[str, Any]] | None = None,
    quota: bool = False,
    units: list[dict[str, Any]] | None = None,
    show_units_left: bool = True,
    sold_out: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Mutates: fija el cupón en el episodio activo (lo crea si no hay).

    `eligible`: los productos a los que aplica (nombre + precios) — la nota
    de cada turno los recuerda para que el bot ofrezca ESOS. `quota`: el
    cupón tiene cupo por unidad (esas combinaciones pueden agotarse).
    `units`: esas combinaciones (handle, color, aroma, precios, cuántas
    quedan) — las leen la nota de cada turno, el selector de variantes y
    `set_order_slot`. `show_units_left`: si el bot puede decir cuántas
    quedan (D3)."""
    episode = get_active_episode(metadata) or ensure_active_episode(
        metadata, now_ms=now_ms
    )
    episode["applied_coupon"] = {
        "code": promotion.code,
        "promotion": asdict(promotion),
        "applied_at_ms": now_ms,
        "eligible_products": list(eligible or []),
        **({"quota": True} if quota else {}),
        **({"units": list(units), "show_units_left": show_units_left} if units else {}),
        **({"sold_out": list(sold_out)} if sold_out else {}),
    }
    return metadata


def clear_applied_coupon(metadata: dict[str, Any]) -> bool:
    episode = get_active_episode(metadata)
    if episode and "applied_coupon" in episode:
        episode.pop("applied_coupon", None)
        return True
    return False


async def discount_line_items(
    catalog: Any, items: list[dict[str, Any]]
) -> list[DiscountLineItem]:
    """Líneas del pedido con la identidad Medusa (product/variant id) que las
    reglas de la promoción entienden. Catálogo caído para un ítem → la línea
    viaja sin ids (solo la selecciona una promo sin filtro de productos)."""
    out: list[DiscountLineItem] = []
    for it in items:
        handle = str(it.get("handle") or "")
        product_id = None
        variant_id = None
        tags: tuple[str, ...] = ()
        if catalog is not None and handle:
            try:
                product = await catalog.get_by_handle(handle)
                product_id = getattr(product, "id", None)
                tags = tuple(str(t) for t in getattr(product, "tags", None) or [])
                variants = getattr(product, "variants", None) or []
                label = it.get("variant_label")
                chosen = next(
                    (v for v in variants if label and getattr(v, "title", None) == label),
                    variants[0] if variants else None,
                )
                variant_id = getattr(chosen, "id", None) if chosen is not None else None
            except Exception:  # noqa: BLE001 — sin ids, la promo global igual aplica
                pass
        out.append(
            DiscountLineItem(
                handle=handle,
                quantity=int(it.get("quantity") or 0),
                unit_price_cop=int(it.get("unit_price_cop") or 0),
                product_id=str(product_id) if product_id else None,
                variant_id=str(variant_id) if variant_id else None,
                tags=tags,
            )
        )
    return out


def quota_product_ids(metadata: dict[str, Any], quotas: Any) -> frozenset[str]:
    """Productos con cupo en el cupón aplicado del episodio: ahí el color y el
    aroma de cada línea deciden el descuento y se validan contra las listas
    del producto. Vacío sin cupón, sin cupo o con el cupo ilegible (en ese
    caso el descuento igual falla cerrado)."""
    raw = applied_coupon(metadata)
    if raw is None or quotas is None:
        return frozenset()
    try:
        sheet = quotas.get(promotion_from_snapshot(raw["promotion"]).id)
    except (TypeError, KeyError, QuotaStoreError):
        return frozenset()
    return frozenset(q.product_id for q in sheet.quotas)


async def coupon_discount_for_items(
    metadata: dict[str, Any],
    catalog: Any,
    items: list[dict[str, Any]],
    *,
    shipping_cop: int = 0,
    quotas: Any = None,
    sales: Any = None,
    variants: list[ItemVariant] | None = None,
) -> AppliedDiscount | None:
    """Descuento del cupón aplicado en el episodio sobre `items`; None si
    no hay cupón aplicado.

    Con cupo por unidad (`quotas` con filas para el cupón) el descuento va
    SOLO a las unidades de las combinaciones que quedan, leyendo lo vendido
    fresco (`sales`); sin poder leerlo, no descuenta (falla cerrada)."""
    raw = applied_coupon(metadata)
    if raw is None:
        return None
    promotion = promotion_from_snapshot(raw["promotion"])
    try:
        sheet = quotas.get(promotion.id) if quotas is not None else None
    except QuotaStoreError:
        # Cupo ilegible: NO es "sin cupo" (aplicaría sin límite) — falla cerrada.
        return AppliedDiscount(
            code=promotion.code, discount_cop=0, applicable_handles=[],
            reason="quota_unavailable", min_subtotal_cop=None,
            description=promotion.description, quota=True,
        )
    if sheet is not None and sheet.quotas:
        subtotal = sum(int(it.get("unit_price_cop") or 0) * int(it.get("quantity") or 0) for it in items)
        if promotion.min_subtotal_cop is not None and subtotal < promotion.min_subtotal_cop:
            return AppliedDiscount(
                code=promotion.code, discount_cop=0, applicable_handles=[],
                reason="min_subtotal",
                min_subtotal_cop=promotion.min_subtotal_cop,
                description=promotion.description, quota=True,
            )
        if variants is None:
            variants, _invalid = await resolve_item_variants(catalog, items, metadata)
        # Alcance real del cupón (productos Y etiquetas) sobre cada línea.
        per_unit = replace(promotion, min_subtotal_cop=None)
        eligible = {
            i
            for i, line in enumerate(await discount_line_items(catalog, items))
            if compute_discount(per_unit, [replace(line, quantity=1)]).discount_cop > 0
        }
        split = await quota_split(
            promotion, items, variants, sheet=sheet, sales=sales, eligible=eligible
        )
        return AppliedDiscount(
            code=promotion.code,
            discount_cop=sum(d.units * d.discount_unit_cop for d in split.line_discounts),
            applicable_handles=sorted(
                {str(items[d.index].get("handle") or "") for d in split.line_discounts}
            ),
            reason=split.reason,
            min_subtotal_cop=None,
            description=promotion.description,
            line_discounts=split.line_discounts,
            quota=True,
            missing_attributes=split.missing_attributes,
        )
    lines = await discount_line_items(catalog, items)
    result = compute_discount(promotion, lines, shipping_cop=shipping_cop)
    return AppliedDiscount(
        code=promotion.code,
        discount_cop=result.discount_cop,
        applicable_handles=list(result.applicable_handles),
        reason=result.reason,
        min_subtotal_cop=result.min_subtotal_cop,
        description=promotion.description,
        line_discounts=result.line_discounts,
    )


def describe_promotion(promotion: PromotionDTO) -> str:
    """"15%" / "$5.000" / "$5.000 por unidad" — para el cliente (solo cupones
    de productos: los de envío no se aplican ni se ofrecen)."""
    if promotion.discount_type == "percentage":
        label = f"{promotion.value}%"
    elif promotion.discount_type == "fixed":
        label = format_cop(promotion.value)
        if promotion.allocation == "each":
            label += " por unidad"
    else:
        label = "promoción especial (la aplica el equipo)"
    return label


def format_cop(amount: int) -> str:
    return "$" + f"{int(amount):,}".replace(",", ".")


#: Con lo que el cliente habla de un cupón (texto normalizado, sin tildes).
_COUPON_WORDS = re.compile(
    r"\b(cupon(es)?|descuentos?|promos?|promocion(es)?|ofertas?|rebajas?|codigo)\b|\d+ ?%"
)
#: Palabras de un nombre de producto que no lo distinguen de los demás.
_GENERIC_WORDS = frozenset({"vela", "velon", "love", "mini", "caja", "pack", "para"})
#: El cupo nunca limita la venta (pedido del operador, 2026-09-24).
NO_LIMIT_TEXT = (
    "El cupo solo dice cuántas unidades llevan descuento, no limita la venta: cualquier "
    "color, aroma o cantidad del catálogo se vende, a precio normal y sin límite."
)


def _mentions(said: str, phrase: str) -> bool:
    """¿El texto normalizado nombra `phrase` como palabra completa (o su plural)?"""
    return bool(phrase) and re.search(rf"\b{re.escape(phrase)}(?:s|es)?\b", said) is not None


def _names_product(said: str, title: str) -> bool:
    """"el cubo" nombra el Cubo Love; "velas" no nombra la Vela Buda."""
    key = product_key(title)
    words = [w for w in key.split() if len(w) >= 4 and w not in _GENERIC_WORDS]
    return any(_mentions(said, w) for w in words) if words else _mentions(said, key)


def _coupon_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Las combinaciones del cupo: las que quedan y las agotadas."""
    return [u for key in ("units", "sold_out") for u in raw.get(key) or [] if isinstance(u, dict)]


def _coupon_titles(raw: dict[str, Any]) -> list[str]:
    """Los productos del cupón. Con cupo, los de sus filas (`eligible_products`
    trae "Producto Color · Aroma", que nombraría el aroma suelto)."""
    if raw.get("quota") or raw.get("units") or raw.get("sold_out"):
        source = _coupon_rows(raw)
    else:
        source = [p for p in raw.get("eligible_products") or [] if isinstance(p, dict)]
    titles = (str(p.get("title") or "").strip() for p in source)
    return list(dict.fromkeys(t for t in titles if t))


def coupon_in_play(metadata: dict[str, Any], text: str | None) -> bool:
    """¿Este mensaje toca el cupón aplicado del episodio?

    Sí si nombra el cupón (código, "cupón", "descuento", "promo", "10 %"), un
    producto del cupón o una combinación color + aroma de su cupo, o si el
    producto del que se está hablando en el pedido es del cupón. Un cupón de
    todo el catálogo siempre está en juego. Si no, el turno es del catálogo
    normal: el webhook no relee el cupo y la nota solo lo recuerda en una
    línea (pedido del operador, 2026-09-24). Ante la duda dice que sí: eso
    cuesta una lectura compartida y una nota más larga, nunca una venta."""
    raw = applied_coupon(metadata)
    if raw is None:
        return False
    try:
        promotion = promotion_from_snapshot(raw["promotion"])
    except (TypeError, KeyError):
        return False
    if promotion.target_type == "shipping_methods":
        return False
    if is_whole_catalog(promotion):
        return True
    said = product_key(text)
    if _mentions(said, product_key(raw.get("code"))) or _COUPON_WORDS.search(said):
        return True
    titles = _coupon_titles(raw)
    if any(_names_product(said, title) for title in titles):
        return True
    if any(
        row.get("color") and row.get("aroma")
        and _mentions(said, product_key(row["color"]))
        and _mentions(said, product_key(row["aroma"]))
        for row in _coupon_rows(raw)
    ):
        return True
    draft = (get_active_episode(metadata) or {}).get("order_draft")
    item = current_item(draft if isinstance(draft, dict) else None, draft_items(draft))
    return item is not None and product_key(item.get("producto")) in {
        product_key(title) for title in titles
    }


def _join_names(names: list[str]) -> str:
    """"Cubo Love, Vela Buda y Cubo de corazón" ("" sin nombres)."""
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} y {names[-1]}"


def build_coupon_note(metadata: dict[str, Any], *, in_play: bool = True) -> str | None:
    """Nota de contexto por turno: el LLM recuerda el cupón aplicado.

    `in_play` (`coupon_in_play`): el mensaje habla del cupón. Si no, la nota
    lo recuerda en una línea, sin combinaciones ni cuántas quedan, y le deja
    el turno al catálogo normal."""
    raw = applied_coupon(metadata)
    if raw is None:
        return None
    try:
        promotion = promotion_from_snapshot(raw["promotion"])
    except (TypeError, KeyError):
        return None
    if promotion.target_type == "shipping_methods":
        # Guardado antes de la decisión del operador (2026-09-23): no aplica.
        return (
            f"[CUPÓN SIN EFECTO: {promotion.code} es de envío y el envío lo cobra "
            "la transportadora a su tarifa, sin descuentos. No lo apliques; si el "
            "cliente lo menciona, explícaselo con amabilidad.]"
        )
    eligible = [p for p in (raw.get("eligible_products") or []) if isinstance(p, dict)]
    units = [u for u in (raw.get("units") or []) if isinstance(u, dict)]
    sold_out = [u for u in (raw.get("sold_out") or []) if isinstance(u, dict)]
    if raw.get("exhausted"):
        # Relectura del cupo (cada mensaje): se vendieron todas.
        scope = (
            "ya no quedan unidades con descuento de este cupón: todo va a precio "
            "normal. Si el cliente pregunta, díselo con honestidad y no inventes "
            "otro descuento."
        )
    elif is_whole_catalog(promotion):
        scope = "aplica a todo el catálogo."
    elif not in_play and (units or eligible):
        # El cliente habla de otra cosa: el cupón no se mete en la charla.
        names = _join_names(_coupon_titles(raw)) or "algunos productos"
        where = f"algunas combinaciones de {names}" if units else names
        scope = (
            f"vale solo en {where}. Si el cliente habla de otra cosa, atiéndelo con el "
            "catálogo normal: todos los colores, aromas y cantidades, a precio normal y sin "
            "límite; no le metas el cupón en la conversación. Si vuelve al cupón o a esos "
            "productos, el sistema te recuerda qué lleva descuento."
        )
    elif units:
        # Cupo por unidad (conversación de prueba del 2026-09-24): las
        # combinaciones por producto y qué dice el cupo de lo ya elegido.
        episode = get_active_episode(metadata) or {}
        show = bool(raw.get("show_units_left", True))
        scope = " ".join(
            [
                "vale SOLO en estas combinaciones mientras queden unidades con descuento "
                "(la confirmación dice cuáles quedan): "
                f"{combos_by_product_text(units, show_units_left=show)}. Si el cliente viene "
                "por el cupón, ofrécelas primero; otra combinación va a precio normal: dilo "
                "antes de tomar el pedido. Si pide más unidades de las que quedan con "
                f"descuento, las demás van a precio normal: dilo también. {NO_LIMIT_TEXT}",
                *draft_vs_coupon_lines(
                    episode.get("order_draft"), units, show_units_left=show, sold_out=sold_out
                ),
            ]
        )
    elif eligible:
        scope = (
            f"aplica SOLO a: {eligible_products_text(eligible)}. Si el cliente viene por el "
            "cupón, ofrece estos productos; lo demás del catálogo se vende a precio normal, "
            "sin límite. Lo que se habló antes de otros productos va SIN descuento — "
            "retómalo solo si el cliente lo pide, aclarándolo."
        )
        if raw.get("quota"):
            # Cupo por unidad: esas combinaciones pueden agotarse después de
            # aplicado el cupón (premortem B3).
            scope += (
                " Esas combinaciones valen mientras queden unidades con descuento: la "
                "confirmación dice cuáles quedan."
            )
    else:
        scope = (
            "aplica solo a algunos productos: confirma cuáles con "
            "`list_promotions` antes de prometer descuento en uno."
        )
    return (
        f"[CUPÓN APLICADO: {promotion.code} — {describe_promotion(promotion)}; "
        f"{scope} El sistema calcula el descuento en "
        "`present_order_confirmation` y `register_order`; usa el total de esos "
        "envelopes. No prometas otro descuento.]"
    )


__all__ = [
    "NO_LIMIT_TEXT",
    "AppliedDiscount",
    "applied_coupon",
    "build_coupon_note",
    "clear_applied_coupon",
    "coupon_discount_for_items",
    "coupon_in_play",
    "describe_promotion",
    "discount_line_items",
    "eligible_products",
    "eligible_products_text",
    "is_whole_catalog",
    "format_cop",
    "promotion_from_snapshot",
    "quota_product_ids",
    "set_applied_coupon",
]
