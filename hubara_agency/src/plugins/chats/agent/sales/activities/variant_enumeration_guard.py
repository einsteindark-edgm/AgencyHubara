"""Guarda de enumeración de variantes — activity del workflow Sales.

Run 9bd495be (2026-09-14): el LLM listó los 11 aromas como texto plano. Si el
texto final del turno enumera 4+ aromas o colores del catálogo y el turno no
emitió `present_variant_picker`, esta activity encola el picker (mismo intent
que la tool: formato curado con emojis) y devuelve True; el workflow suprime
el texto plano y el flush entrega el picker. Sin enumeración, o con el
catálogo caído, devuelve False y el turno sigue como siempre (nunca bloquea).

DEHA: R-STATELESS / R-JSON (in str×2, out bool) / R-DIP (catálogo por
composition root, vault por `_append_intent`).
"""
from __future__ import annotations

from temporalio import activity

from src.sdk.connectorkit import get_catalog_client, parse_variant_tags
from src.plugins.chats.agent.sales.tools.ui_intents import (
    _append_intent,
    build_variant_picker_intent,
)
from src.plugins.chats.agent.sales.variant_enumeration import (
    default_intro,
    find_enumerated_variants,
    intro_before,
)


async def _catalog_labels() -> tuple[list[str], list[str]]:
    catalog = get_catalog_client()
    result = await catalog.search(q="", limit=30)
    aromas: list[str] = []
    colors: list[str] = []
    seen_a: set[str] = set()
    seen_c: set[str] = set()
    for product in list(result.results):
        attrs = parse_variant_tags(getattr(product, "tags", None))
        for label in attrs.aromas:
            if label.casefold() not in seen_a:
                seen_a.add(label.casefold())
                aromas.append(label)
        for label in attrs.colors:
            if label.casefold() not in seen_c:
                seen_c.add(label.casefold())
                colors.append(label)
    return aromas, colors


@activity.defn(name="apply_variant_enumeration_guard")
async def apply_variant_enumeration_guard_activity(session_id: str, final_text: str) -> bool:
    if not final_text or not final_text.strip():
        return False
    try:
        aromas, colors = await _catalog_labels()
    except Exception as exc:  # noqa: BLE001 — catálogo caído: no bloquear el turno
        activity.logger.warning(
            "variant_enumeration_guard: catálogo no disponible (%s) — texto sin cambios",
            exc,
        )
        return False
    hit = find_enumerated_variants(final_text, aromas=aromas, colors=colors)
    if hit is None:
        return False
    variant_type, labels = hit
    intro = intro_before(final_text, labels) or default_intro(variant_type)
    intent = build_variant_picker_intent(
        variant_type=variant_type, labels=labels, intro_text=intro, handle=None
    )
    if intent is None:
        return False
    _append_intent(session_id, intent)
    activity.logger.info(
        "variant_enumeration_guard: %d %s enumerados en texto → picker encolado (session=%s)",
        len(labels),
        variant_type,
        session_id,
    )
    return True


__all__ = ("apply_variant_enumeration_guard_activity",)
