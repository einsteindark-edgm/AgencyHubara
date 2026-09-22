"""Cupón aplicado en el episodio — snapshot + descuento recomputado (puro +
una lectura del catálogo).

El cliente da un código → `apply_coupon` lo valida contra Medusa y guarda en
`episodes[-1].applied_coupon` el SNAPSHOT de la promoción. De ahí en adelante
el descuento se RECOMPUTA (confirmación, registro, dashboard) con ese
snapshot y los precios del catálogo: el LLM nunca decide el monto (L-19), y
Medusa puede cambiar a mitad de chat sin que el pedido cambie de precio.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

from src.sdk.connectorkit import (
    DiscountLineItem,
    PromotionDTO,
    compute_discount,
)

from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    ensure_active_episode,
    get_active_episode,
)


@dataclass(frozen=True)
class AppliedDiscount:
    code: str
    discount_cop: int
    applicable_handles: list[str]
    applies_to_shipping: bool
    #: None = aplicó; "no_applicable_items" | "min_subtotal" | "unsupported"
    reason: str | None
    min_subtotal_cop: int | None
    description: str | None


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
    for key in ("product_ids", "variant_ids", "collection_ids"):
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
    return not (promotion.product_ids or promotion.variant_ids or promotion.collection_ids)


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
    for product in getattr(result, "results", None) or []:
        pid = str(getattr(product, "id", "") or "")
        variants = list(getattr(product, "variants", None) or [])
        matching = [v for v in variants if str(getattr(v, "id", "")) in promotion.variant_ids]
        if pid not in promotion.product_ids and not matching:
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
) -> dict[str, Any]:
    """Mutates: fija el cupón en el episodio activo (lo crea si no hay).

    `eligible`: los productos a los que aplica (nombre + precios) — la nota
    de cada turno los recuerda para que el bot ofrezca ESOS."""
    episode = get_active_episode(metadata) or ensure_active_episode(
        metadata, now_ms=now_ms
    )
    episode["applied_coupon"] = {
        "code": promotion.code,
        "promotion": asdict(promotion),
        "applied_at_ms": now_ms,
        "eligible_products": list(eligible or []),
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
        if catalog is not None and handle:
            try:
                product = await catalog.get_by_handle(handle)
                product_id = getattr(product, "id", None)
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
            )
        )
    return out


async def coupon_discount_for_items(
    metadata: dict[str, Any],
    catalog: Any,
    items: list[dict[str, Any]],
    *,
    shipping_cop: int = 0,
) -> AppliedDiscount | None:
    """Descuento del cupón aplicado en el episodio sobre `items`; None si
    no hay cupón aplicado."""
    raw = applied_coupon(metadata)
    if raw is None:
        return None
    promotion = promotion_from_snapshot(raw["promotion"])
    lines = await discount_line_items(catalog, items)
    result = compute_discount(promotion, lines, shipping_cop=shipping_cop)
    return AppliedDiscount(
        code=promotion.code,
        discount_cop=result.discount_cop,
        applicable_handles=list(result.applicable_handles),
        applies_to_shipping=result.applies_to_shipping,
        reason=result.reason,
        min_subtotal_cop=result.min_subtotal_cop,
        description=promotion.description,
    )


def describe_promotion(promotion: PromotionDTO) -> str:
    """"15%" / "$5.000" / "$5.000 por unidad" / "envío" — para el cliente."""
    if promotion.discount_type == "percentage":
        label = f"{promotion.value}%"
    elif promotion.discount_type == "fixed":
        label = format_cop(promotion.value)
        if promotion.allocation == "each":
            label += " por unidad"
    else:
        label = "promoción especial (la aplica el equipo)"
    if promotion.target_type == "shipping_methods":
        label += " en el envío"
    return label


def format_cop(amount: int) -> str:
    return "$" + f"{int(amount):,}".replace(",", ".")


def build_coupon_note(metadata: dict[str, Any]) -> str | None:
    """Nota de contexto por turno: el LLM recuerda el cupón aplicado."""
    raw = applied_coupon(metadata)
    if raw is None:
        return None
    try:
        promotion = promotion_from_snapshot(raw["promotion"])
    except (TypeError, KeyError):
        return None
    eligible = [p for p in (raw.get("eligible_products") or []) if isinstance(p, dict)]
    if is_whole_catalog(promotion):
        scope = "aplica a todo el catálogo."
    elif eligible:
        scope = (
            f"aplica SOLO a: {eligible_products_text(eligible)}. Ofrece estos "
            "productos; lo que se habló antes de otros productos va SIN "
            "descuento — retómalo solo si el cliente lo pide, aclarándolo."
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
    "AppliedDiscount",
    "applied_coupon",
    "build_coupon_note",
    "clear_applied_coupon",
    "coupon_discount_for_items",
    "describe_promotion",
    "discount_line_items",
    "eligible_products",
    "eligible_products_text",
    "is_whole_catalog",
    "format_cop",
    "promotion_from_snapshot",
    "set_applied_coupon",
]
