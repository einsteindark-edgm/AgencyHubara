"""Cupón aplicado en el episodio — snapshot + descuento recomputado (puro +
una lectura del catálogo).

El cliente da un código → `apply_coupon` lo valida contra Medusa y guarda en
`episodes[-1].applied_coupon` el SNAPSHOT de la promoción. De ahí en adelante
el descuento se RECOMPUTA (confirmación, registro, dashboard) con ese
snapshot y los precios del catálogo: el LLM nunca decide el monto (L-19), y
Medusa puede cambiar a mitad de chat sin que el pedido cambie de precio.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
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
    fields = PromotionDTO.__dataclass_fields__
    return PromotionDTO(**{k: data.get(k) for k in fields})


def set_applied_coupon(
    metadata: dict[str, Any], *, promotion: PromotionDTO, now_ms: int
) -> dict[str, Any]:
    """Mutates: fija el cupón en el episodio activo (lo crea si no hay)."""
    episode = get_active_episode(metadata) or ensure_active_episode(
        metadata, now_ms=now_ms
    )
    episode["applied_coupon"] = {
        "code": promotion.code,
        "promotion": asdict(promotion),
        "applied_at_ms": now_ms,
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
    return (
        f"[CUPÓN APLICADO: {promotion.code} — {describe_promotion(promotion)}. "
        "El sistema calcula el descuento en `present_order_confirmation` y "
        "`register_order`; usa el total que devuelven esos envelopes. No "
        "prometas otro descuento.]"
    )


__all__ = [
    "AppliedDiscount",
    "applied_coupon",
    "build_coupon_note",
    "clear_applied_coupon",
    "coupon_discount_for_items",
    "describe_promotion",
    "discount_line_items",
    "format_cop",
    "promotion_from_snapshot",
    "set_applied_coupon",
]
