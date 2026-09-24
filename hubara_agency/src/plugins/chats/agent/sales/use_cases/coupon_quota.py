"""Cupo por unidad de un cupón, visto por el bot de ventas.

Un cupón con filas de cupo (Marketing → Cupones) aplica SOLO a esas
combinaciones producto + color + aroma mientras queden unidades. Las
vendidas se derivan de los pedidos (SDK `quota_board`); si no se pueden leer,
el cupón con cupo NO se aplica (falla cerrada). Sin filas, el cupón se
comporta como siempre.

Los precios salen del catálogo y el descuento de `compute_discount` (L-19):
el LLM solo repite lo que dice el envelope.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import get_active_episode
from src.plugins.chats.shared.draft_items import draft_items, product_key
from src.sdk.connectorkit import (
    REASON_QUOTA_EXHAUSTED,
    DiscountLineItem,
    LineDiscount,
    PromotionDTO,
    PromotionsUnavailableError,
    QuotaLine,
    QuotaStatus,
    QuotaStoreError,
    allocate_units,
    compute_discount,
    match_option,
    parse_variant_tags,
    quota_board,
    quota_exhausted,
)

REASON_QUOTA_UNAVAILABLE = "quota_unavailable"
#: El cupo exige color/aroma y la línea no lo dice (falla cerrada).
REASON_MISSING_ATTRIBUTES = "missing_attributes"


def format_cop(amount: int) -> str:
    return "$" + f"{int(amount):,}".replace(",", ".")


@dataclass(frozen=True)
class QuotaOffer:
    """Lo que el bot puede decir del cupo de un cupón.

    `reason`: None (hay unidades) · "quota_exhausted" · "quota_unavailable".
    `units`: las combinaciones con unidades, con precio y precio con descuento
    (y `units_left` solo si el cupón permite decirlo, D3)."""

    has_quota: bool
    reason: str | None = None
    units: tuple[dict[str, Any], ...] = ()
    show_units_left: bool = True


_NO_QUOTA = QuotaOffer(has_quota=False)


def _cop_price(product: Any) -> int | None:
    for variant in getattr(product, "variants", None) or []:
        for price in getattr(variant, "prices", None) or []:
            if str(getattr(price, "currency_code", "")).lower() == "cop":
                try:
                    return int(round(float(price.amount)))
                except (TypeError, ValueError):
                    return None
    return None


async def _catalog_by_id(catalog: Any) -> dict[str, tuple[int, tuple[str, ...]]]:
    """Precio COP y etiquetas de cada producto del catálogo, por id."""
    if catalog is None:
        return {}
    try:
        result = await catalog.search("", limit=500)
    except Exception:  # noqa: BLE001 — sin catálogo no hay precio (no se ofrece)
        return {}
    out: dict[str, tuple[int, tuple[str, ...]]] = {}
    for product in getattr(result, "results", None) or []:
        price = _cop_price(product)
        if price is not None:
            tags = tuple(str(t) for t in getattr(product, "tags", None) or [])
            out[str(getattr(product, "id", ""))] = (price, tags)
    return out


def _unit(
    status: QuotaStatus, promotion: PromotionDTO, price: int, tags: tuple[str, ...], *, show: bool
) -> dict[str, Any] | None:
    """La unidad con su precio con descuento, calculado con el alcance REAL
    del cupón (productos Y etiquetas). None si el cupón no cubre ese producto
    (una fila fuera de alcance no se ofrece)."""
    quota = status.quota
    per_unit = replace(promotion, min_subtotal_cop=None)
    line = DiscountLineItem(handle=quota.handle, quantity=1, unit_price_cop=price,
                            product_id=quota.product_id, tags=tags)
    discount = compute_discount(per_unit, [line]).discount_cop
    if discount <= 0:
        return None
    unit: dict[str, Any] = {"title": quota.title, "color": quota.color, "aroma": quota.aroma}
    if show:
        unit["units_left"] = status.units_left
    unit["price_cop"] = price
    unit["discounted_price_cop"] = max(price - discount, 0)
    return unit


async def quota_offer(promotion: PromotionDTO, *, quotas: Any, sales: Any, catalog: Any) -> QuotaOffer:
    """El cupo del cupón para el bot. Sin `quotas` (worker viejo) o sin filas:
    `has_quota=False` y el cupón aplica como siempre. Todo lo que impide
    saber qué unidades quedan (cupo ilegible, Medusa caído, catálogo sin
    precios) es `quota_unavailable`: falla CERRADA."""
    if quotas is None:
        return _NO_QUOTA
    try:
        sheet = quotas.get(promotion.id)
    except QuotaStoreError:
        return QuotaOffer(True, REASON_QUOTA_UNAVAILABLE)
    if not sheet.quotas:
        return _NO_QUOTA
    if sales is None or promotion.discount_type != "percentage":
        return QuotaOffer(True, REASON_QUOTA_UNAVAILABLE, show_units_left=sheet.show_units_left)
    try:
        board = await quota_board(sheet, sales)
    except PromotionsUnavailableError:
        return QuotaOffer(True, REASON_QUOTA_UNAVAILABLE, show_units_left=sheet.show_units_left)
    if quota_exhausted(board) == REASON_QUOTA_EXHAUSTED:
        return QuotaOffer(True, REASON_QUOTA_EXHAUSTED, show_units_left=sheet.show_units_left)
    known = await _catalog_by_id(catalog)
    units = tuple(
        unit
        for s in board
        if s.units_left > 0 and s.quota.product_id in known
        for unit in [_unit(s, promotion, *known[s.quota.product_id], show=sheet.show_units_left)]
        if unit is not None
    )
    if not units:
        return QuotaOffer(True, REASON_QUOTA_UNAVAILABLE, show_units_left=sheet.show_units_left)
    return QuotaOffer(True, None, units, sheet.show_units_left)


def unit_label(unit: dict[str, Any]) -> str:
    """"Cubo Love Rosado · Café"."""
    variant = " · ".join(v for v in (unit.get("color"), unit.get("aroma")) if v)
    return f"{unit['title']} {variant}".strip()


def units_text(units: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> str:
    """"Cubo Love Rosado · Café ($21.000 → $18.900, quedan 3)"."""
    parts = []
    for unit in units:
        detail = f"{format_cop(unit['price_cop'])} → {format_cop(unit['discounted_price_cop'])}"
        if "units_left" in unit:
            detail += f", quedan {unit['units_left']}"
        parts.append(f"{unit_label(unit)} ({detail})")
    return ", ".join(parts)


def as_eligible(units: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """Las unidades en la forma de `eligible_products` (la nota de cada turno
    recuerda ESTAS combinaciones, no el producto entero)."""
    return [
        {
            "handle": "",
            "title": unit_label(u),
            "price_cop": u["price_cop"],
            "discounted_price_cop": u["discounted_price_cop"],
        }
        for u in units
    ]




# ---------------------------------------------------------------------------
# Confirmación y registro: color/aroma por ítem + reparto del cupo.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemVariant:
    """Producto de una línea del pedido con su color/aroma canónicos."""

    product_id: str | None
    title: str
    color: str | None
    aroma: str | None
    #: Las listas del producto (sus etiquetas), para pedirle al cliente.
    colors: tuple[str, ...] = ()
    aromas: tuple[str, ...] = ()


#: "Café, Lavanda" / "Rosado y Azul" / "Café/Lavanda": varios valores en uno.
_SEVERAL = re.compile(r"\s*(?:,|/|\+|\by\b)\s*", re.IGNORECASE)


@dataclass(frozen=True)
class InvalidAttribute:
    index: int
    field: str
    value: str
    title: str
    options: tuple[str, ...]

    def message(self) -> str:
        word = "color" if self.field == "color" else "aroma"
        parts = [t for t in _SEVERAL.split(self.value) if t]
        if len(parts) > 1 and all(match_option(t, list(self.options)) for t in parts):
            return (
                f'{self.title}: "{self.value}" son varios {word}s — para el cupón va una línea por '
                "combinación de color y aroma (con su cantidad)"
            )
        return f'{self.title} no tiene el {word} "{self.value}" (opciones: {", ".join(self.options)})'


def _draft_attrs(metadata: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Color/aroma del borrador estructurado del pedido, por producto."""
    episode = get_active_episode(metadata or {}) or {}
    out: dict[str, dict[str, Any]] = {}
    for item in draft_items(episode.get("order_draft")):
        key = product_key(item.get("producto"))
        if key and key not in out:
            out[key] = item
    return out


async def resolve_item_variants(
    catalog: Any,
    items: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
    *,
    strict_products: frozenset[str] | set[str] = frozenset(),
) -> tuple[list[ItemVariant], list[InvalidAttribute]]:
    """Color y aroma canónicos de cada línea.

    Lo que manda el LLM (`color`/`aroma` del ítem) se busca en las listas
    cerradas del producto (sus etiquetas); si no lo manda, se toma del
    borrador estructurado del pedido cuando coincide con la lista; si no,
    queda None. Solo en los productos de `strict_products` (los que tienen
    cupo en el cupón aplicado) un valor que no está en la lista es un error:
    ahí decide el descuento. En el resto se acepta como antes — lo que guarda
    `set_order_slot` (varios aromas, colores de `metadata.colores`, familias)
    no siempre está en las etiquetas.
    """
    drafts = _draft_attrs(metadata)
    # El borrador guarda UN color/aroma por producto: solo completa una línea
    # si ese producto está una sola vez en el pedido (con dos líneas no se
    # sabe cuál es cuál — premortem C9).
    handles = [str(item.get("handle") or "") for item in items]
    variants: list[ItemVariant] = []
    invalid: list[InvalidAttribute] = []
    for index, item in enumerate(items):
        product = None
        if catalog is not None:
            try:
                product = await catalog.get_by_handle(str(item.get("handle") or ""))
            except Exception:  # noqa: BLE001 — sin producto no hay listas
                product = None
        title = str(getattr(product, "title", "") or item.get("handle") or "")
        attrs = parse_variant_tags(list(getattr(product, "tags", None) or []))
        draft = drafts.get(product_key(title), {}) if handles.count(handles[index]) == 1 else {}
        chosen: dict[str, str | None] = {}
        for field, options in (("color", attrs.colors), ("aroma", attrs.aromas)):
            given = str(item.get(field) or "").strip()
            if not options:
                chosen[field] = None
                continue
            if given:
                canonical = match_option(given, options)
                if canonical is None:
                    invalid.append(InvalidAttribute(index, field, given, title, tuple(options)))
                chosen[field] = canonical
            else:
                chosen[field] = match_option(str(draft.get(field) or ""), options)
        product_id = str(getattr(product, "id", "") or "") or None
        variants.append(
            ItemVariant(
                product_id=product_id,
                title=title,
                color=chosen["color"],
                aroma=chosen["aroma"],
                colors=tuple(attrs.colors),
                aromas=tuple(attrs.aromas),
            )
        )
    invalid = [
        bad for bad in invalid if variants[bad.index].product_id in strict_products
    ]
    return variants, invalid


@dataclass(frozen=True)
class QuotaSplit:
    """El reparto del cupo sobre un pedido."""

    line_discounts: tuple[LineDiscount, ...]
    #: None = aplicó · quota_exhausted · quota_unavailable · missing_attributes
    #: · no_applicable_items
    reason: str | None
    missing_attributes: tuple[int, ...] = ()


async def quota_split(
    promotion: PromotionDTO,
    items: list[dict[str, Any]],
    variants: list[ItemVariant],
    *,
    sheet: Any,
    sales: Any,
    eligible: set[int] | None = None,
) -> QuotaSplit:
    """Qué unidades de cada línea llevan descuento, leyendo lo vendido FRESCO.

    `eligible`: líneas que el cupón cubre (alcance real, con etiquetas); las
    demás no reciben cupo aunque coincidan con una fila. Sin lector de
    vendidas, o si el cupón no es de porcentaje, falla CERRADA."""
    if sales is None or promotion.discount_type != "percentage":
        return QuotaSplit((), REASON_QUOTA_UNAVAILABLE)
    try:
        board = await quota_board(sheet, sales)
    except PromotionsUnavailableError:
        return QuotaSplit((), REASON_QUOTA_UNAVAILABLE)
    lines = [
        QuotaLine(
            product_id=v.product_id or "",
            quantity=int(it.get("quantity") or 0) if eligible is None or i in eligible else 0,
            unit_price_cop=int(it.get("unit_price_cop") or 0),
            color=v.color,
            aroma=v.aroma,
        )
        for i, (it, v) in enumerate(zip(items, variants))
    ]
    allocation = allocate_units(board, lines, percentage=promotion.value)
    discounts = tuple(
        LineDiscount(g.line, g.units, g.discount_unit_cop, quota_id=g.quota_id)
        for g in allocation.grants
    )
    if discounts:
        return QuotaSplit(discounts, None, allocation.missing_attributes)
    if allocation.missing_attributes:
        return QuotaSplit((), REASON_MISSING_ATTRIBUTES, allocation.missing_attributes)
    if quota_exhausted(board) == REASON_QUOTA_EXHAUSTED:
        return QuotaSplit((), REASON_QUOTA_EXHAUSTED)
    return QuotaSplit((), "no_applicable_items")


def _line_identity(item: dict[str, Any], variant: ItemVariant) -> tuple[str, str, str, int]:
    """Qué es una línea para el cupo: producto, color y aroma canónicos y precio."""
    return (
        str(item.get("handle") or ""),
        variant.color or "",
        variant.aroma or "",
        int(item.get("unit_price_cop") or 0),
    )


def split_key(
    line_discounts: tuple[LineDiscount, ...] | list[Any],
    items: list[dict[str, Any]],
    variants: list[ItemVariant],
) -> list[list[Any]]:
    """Forma comparable (y JSON) del reparto SIN depender del orden de las
    líneas: `[[handle, color, aroma, precio, unidades, descuento, cupo]]`, con
    las unidades de líneas iguales sumadas. El mismo pedido con los ítems en
    otro orden tiene el mismo reparto."""
    units: dict[tuple[Any, ...], int] = {}
    for d in line_discounts:
        ident = (*_line_identity(items[d.index], variants[d.index]), int(d.discount_unit_cop), d.quota_id or "")
        units[ident] = units.get(ident, 0) + int(d.units)
    return sorted(
        [handle, color, aroma, price, n, discount, quota]
        for (handle, color, aroma, price, discount, quota), n in units.items()
    )


def line_discounts_from_key(
    key: list[list[Any]], items: list[dict[str, Any]], variants: list[ItemVariant]
) -> tuple[LineDiscount, ...] | None:
    """El reparto `key` (de `split_key`) puesto sobre ESTAS líneas: cada
    entrada va a las líneas iguales, sin pasar su cantidad. None si no cabe
    (no es el pedido que se confirmó)."""
    room = [int(it.get("quantity") or 0) for it in items]
    idents = [_line_identity(it, v) for it, v in zip(items, variants)]
    out: list[LineDiscount] = []
    for entry in key:
        try:
            handle, color, aroma, price, n, discount, quota = entry
            pending, ident = int(n), (handle, color, aroma, int(price))
        except (TypeError, ValueError):
            return None
        for index, line in enumerate(idents):
            if pending <= 0:
                break
            if line != ident or room[index] <= 0:
                continue
            take = min(pending, room[index])
            room[index] -= take
            pending -= take
            out.append(LineDiscount(index, take, int(discount), quota_id=quota or None))
        if pending > 0:
            return None
    return tuple(sorted(out, key=lambda d: d.index))


#: Clave del episodio con el reparto que se le mostró al cliente en la
#: confirmación (register_order lo compara con el reparto fresco).
CONFIRMED_SPLIT_KEY = "coupon_confirmed_split"


def remember_confirmed_split(
    metadata: dict[str, Any], code: str, split: list[list[Any]]
) -> dict[str, Any] | None:
    """Mutator de `store.update`: guarda el reparto en el episodio activo.
    Sin episodio activo (o una lectura vacía de un archivo a medio escribir)
    devuelve None → no se escribe (nunca se pisa la sesión con `{}`)."""
    episode = get_active_episode(metadata)
    if episode is None:
        return None
    episode[CONFIRMED_SPLIT_KEY] = {"code": code, "split": split}
    return metadata


def confirmed_split(metadata: dict[str, Any], code: str) -> list[list[Any]] | None:
    raw = (get_active_episode(metadata) or {}).get(CONFIRMED_SPLIT_KEY)
    if not isinstance(raw, dict) or raw.get("code") != code or not isinstance(raw.get("split"), list):
        return None
    return sorted([list(x) for x in raw["split"]])


def missing_attributes_text(variants: list[ItemVariant], indices: tuple[int, ...] | list[int]) -> str:
    """"Cubo Love: color (Rosado, Azul) y aroma (Café, Lavanda)"."""
    parts = []
    for index in dict.fromkeys(indices):
        variant = variants[index]
        need = [
            f"{word} ({', '.join(options)})"
            for word, value, options in (
                ("color", variant.color, variant.colors),
                ("aroma", variant.aroma, variant.aromas),
            )
            if options and not value
        ]
        parts.append(f"{variant.title}: {' y '.join(need)}" if need else variant.title)
    return "; ".join(parts)


def split_summary(
    code: str, items: list[dict[str, Any]], variants: list[ItemVariant], split: Any
) -> str:
    """"1 × Cubo Love Rosado · Café con AMOR26 (−$2.100); 1 a precio normal"."""
    parts: list[str] = []
    for index, (item, variant) in enumerate(zip(items, variants)):
        label = unit_label({"title": variant.title, "color": variant.color, "aroma": variant.aroma})
        discounted = [d for d in split.line_discounts if d.index == index]
        units = sum(d.units for d in discounted)
        if not units:
            continue
        amount = sum(d.units * d.discount_unit_cop for d in discounted)
        text = f"{units} × {label} con {code} (−{format_cop(amount)})"
        rest = int(item.get("quantity") or 0) - units
        if rest > 0:
            text += f"; {rest} a precio normal"
        parts.append(text)
    return "; ".join(parts)


__all__ = [
    "CONFIRMED_SPLIT_KEY",
    "REASON_MISSING_ATTRIBUTES",
    "REASON_QUOTA_UNAVAILABLE",
    "InvalidAttribute",
    "ItemVariant",
    "QuotaOffer",
    "QuotaSplit",
    "confirmed_split",
    "line_discounts_from_key",
    "missing_attributes_text",
    "quota_split",
    "remember_confirmed_split",
    "resolve_item_variants",
    "split_key",
    "split_summary",
    "as_eligible",
    "quota_offer",
    "unit_label",
    "units_text",
]
