"""Contexto de catálogo del scorecard (HU-SC-1).

Listas cerradas del snapshot local (el mismo que leen las tools del agente):
aromas y colores para VAR-01, títulos para DES-05 y un resumen de texto para
el juez de DES-06. Catálogo caído → `catalog_available=False` y los checks que
lo necesitan devuelven `desconocido` (nunca adivinan).

Nota: el snapshot es el VIGENTE, que puede diferir del que existía cuando
ocurrió la conversación (mismo límite que el evaluador legado).
"""
from __future__ import annotations

from typing import Any

from src.plugins.chats.agent.sales_eval.scorecard.model import CheckContext

_SEARCH_LIMIT = 100


def _dedupe(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        key = v.casefold()
        if v and key not in seen:
            seen.add(key)
            out.append(v)
    return tuple(out)


async def build_check_context(catalog: Any | None = None) -> CheckContext:
    from src.sdk.connectorkit import get_catalog_client, parse_variant_tags

    try:
        client = catalog if catalog is not None else get_catalog_client()
        result = await client.search(q="", limit=_SEARCH_LIMIT)
        products = list(result.results)
    except Exception:  # noqa: BLE001 — sin catálogo los checks dicen desconocido
        return CheckContext()
    aromas: list[str] = []
    colors: list[str] = []
    titles: list[str] = []
    lines: list[str] = []
    for p in products:
        attrs = parse_variant_tags(getattr(p, "tags", None))
        aromas.extend(attrs.aromas)
        colors.extend(attrs.colors)
        titles.append(str(p.title))
        price = ""
        variants = getattr(p, "variants", None) or []
        prices = getattr(variants[0], "prices", None) if variants else None
        if prices:
            price = f" — ${prices[0].amount} {str(prices[0].currency_code).upper()}"
        lines.append(
            f"• {p.title}{price} — aromas: {', '.join(attrs.aromas) or 'ninguno'}"
            f" — colores: {', '.join(attrs.colors) or 'ninguno'}"
        )
    if not products:
        return CheckContext()
    return CheckContext(
        aromas=_dedupe(aromas),
        colors=_dedupe(colors),
        product_titles=_dedupe(titles),
        catalog_available=True,
        catalog_summary="\n".join(lines),
    )
