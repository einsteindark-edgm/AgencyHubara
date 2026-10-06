"""Builtins de los mapeos al catálogo (categoría, familia de color, ítem del
pedido, producto nombrado, enumeración) — PAQUETES_DE_DECISION.md F3.

Las opciones de estas capacidades no se saben al escribir el paquete: las
arma un builtin de clase `options` desde la entrada (`{opción: (etiqueta,
valor)}`): Jev lee la etiqueta y contesta la opción; la tabla lee el valor
como `opt['<pregunta>'][<opción>]`. Una opción que no está en la lista no
pasa.

Se cargan solo cuando una capacidad del paquete los pide (las reglas viven
en `use_cases/` y en el catálogo).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.plugins.chats.agent.sales.decisions.capabilities.respuestas import option_keys
from src.plugins.chats.agent.sales.decisions.context import customer_window
from src.plugins.chats.agent.sales.use_cases.order_draft import item_for_values
from src.plugins.chats.agent.sales.variant_enumeration import (
    MIN_COMBINATIONS,
    enumeration_candidates,
    find_enumerated_variants,
)
from src.sdk.catalogkit import family_of_color, normalize_label, resolve_category, resolve_color_family

Options = dict[str, tuple[str, Any]]


def resolution(res: Any) -> dict[str, Any]:
    """`ColorFamilyResolution | None` → JSON (lo que usa `set_order_slot`)."""
    if res is None:
        return {"estado": None, "color": None, "familias": [], "candidatas": [], "tono": False}
    return {
        "estado": res.status,
        "color": res.canonical,
        "familias": list(res.families),
        "candidatas": list(res.candidates),
        "tono": bool(res.shade_requested),
    }


def _accepting(inp: Any) -> dict[str, int]:
    return {f"item_{k + 1}": k for k, ok in enumerate(inp.aceptan) if ok is True}


# ── reglas ──


def category_of_query(inp: Any) -> dict[str, str | None]:
    """`resolve_category`: exacto → contenido → parecido de texto ≥ 0,80."""
    matched = resolve_category(inp.query, list(inp.categories)).matched
    return {"categoria": matched.slug if matched else None}


def color_family(inp: Any) -> dict[str, Any]:
    """La tabla de familias del tenant (`resolve_color_family`)."""
    return resolution(resolve_color_family(inp.color, list(inp.colores), inp.familias))


def order_item_for_values(inp: Any) -> dict[str, Any]:
    """El ítem en curso, salvo que su producto rechace los datos y otro los acepte."""
    k = item_for_values(inp.actual, list(inp.aceptan))
    return {"item": k, "producto": inp.items[k]}


def named_titles(inp: Any) -> tuple[str, ...]:
    """Los títulos que la charla nombra tal cual."""
    return tuple(inp.named)


def enumerated_variants(inp: Any) -> tuple:
    hit = find_enumerated_variants(inp.text, aromas=list(inp.aromas), colors=list(inp.colors))
    return () if hit is None else (hit[0], tuple(hit[1]))


# ── vistas ──


def enumeration_found(inp: Any) -> dict[str, Any]:
    """Los aromas y colores del catálogo que el texto enumera."""
    found = enumeration_candidates(inp.text, aromas=list(inp.aromas), colors=list(inp.colors))
    return {"found_scents": found.scents, "found_colors": found.colors}


# ── estado ──


def category_request(inp: Any) -> str | None:
    query = (inp.query or "").strip()
    if not query or not inp.categories:
        return None
    labels = {c.slug: c.label for c in inp.categories}
    return f"El cliente pidió ver la categoría: «{query}»\nCategorías del catálogo: {', '.join(labels.values())}"


def color_request(inp: Any) -> str | None:
    if not normalize_label(inp.color or "") or not inp.colores:
        return None
    return "\n".join([
        f"Producto: {inp.producto}" if inp.producto else "Producto del pedido",
        f"Colores del producto: {', '.join(inp.colores)}",
        f"El cliente pidió el color: «{inp.color.strip()}»",
    ])


def order_item_data(inp: Any) -> str | None:
    """Solo cuando dos o más productos del pedido aceptan los datos."""
    candidates = _accepting(inp)
    if len(candidates) < 2:
        return None
    values = ", ".join(f"{field} «{value}»" for field, value in inp.valores.items())
    lines = ["PRODUCTOS DEL PEDIDO", *(f"- {inp.items[k]}" for k in candidates.values()),
             f"DATOS QUE LLEGAN SIN PRODUCTO: {values}"]
    window = customer_window(list(inp.events), burst_wamids=set(), burst_size=0)
    if window.lines:
        lines += ["CONVERSACIÓN — lo último", *window.lines]
    return "\n".join(lines)


def chat_and_catalog(inp: Any) -> str | None:
    if not inp.text.strip() or not inp.titles:
        return None
    return (
        "Conversación de una tienda con un cliente (lo último al final):\n"
        f"{inp.text.strip()}\n\nProductos del catálogo: " + ", ".join(inp.titles) + "."
    )


def text_with_lists(inp: Any) -> str | None:
    """El texto, solo si enumera una lista que decidir (aromas o colores)."""
    from src.plugins.chats.agent.sales.variant_enumeration import MIN_ENUMERATED

    found = enumeration_candidates(inp.text, aromas=list(inp.aromas), colors=list(inp.colors))
    if max(len(found.scents), len(found.colors)) < MIN_ENUMERATED:
        return None
    return f"Texto que la tienda le va a enviar a un cliente por WhatsApp:\n{inp.text.strip()}"


# ── opciones ──


def catalog_categories(inp: Any, *, reserved: Sequence[str]) -> Options:
    """Una opción por categoría del catálogo: etiqueta = su nombre, valor = su slug."""
    labels = {c.slug: c.label for c in inp.categories}
    keys = option_keys([c.slug for c in inp.categories], reserved=reserved)
    return {key: (labels[slug], slug) for key, slug in keys.items()}


def product_colors(inp: Any, *, reserved: Sequence[str]) -> Options:
    """Una opción por color del producto; el valor es la resolución completa
    (`tono`: otras palabras que las del catálogo, el bot confirma el tono)."""
    out: Options = {}
    for key, color in option_keys(inp.colores, reserved=reserved).items():
        family = family_of_color(color, inp.familias)
        out[key] = (color, {
            "estado": "resolved", "color": color, "familias": [family.label if family else color],
            "candidatas": [color], "tono": True,
        })
    return out


def accepting_items(inp: Any, *, reserved: Sequence[str]) -> Options:
    """Los productos del pedido que aceptan los datos (`item_<n>`)."""
    return {key: (inp.items[k], {"item": k, "producto": inp.items[k]}) for key, k in _accepting(inp).items()}


def catalog_titles(inp: Any, *, reserved: Sequence[str]) -> Options:
    return {key: (title, title) for key, title in option_keys(inp.titles, reserved=reserved).items()}


# ── pisos ──


def no_enumeration_with_combinations(inp: Any, rule: Any, jev: Any) -> tuple:
    """Las combinaciones «Color · Aroma» de un cupón nunca van al selector."""
    found = enumeration_candidates(inp.text, aromas=list(inp.aromas), colors=list(inp.colors))
    return () if found.combinations >= MIN_COMBINATIONS else jev
