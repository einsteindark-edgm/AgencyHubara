"""Guarda de enumeración de variantes — activity del workflow Sales.

Run 9bd495be (2026-09-14): el LLM listó los 11 aromas como texto plano. Si el
texto final del turno enumera 4+ aromas o colores del catálogo y el turno no
emitió `present_variant_picker`, esta activity encola el picker (mismo intent
que la tool: formato curado con emojis) y devuelve el texto que va a recibir
el cliente; el workflow suprime el texto plano y el flush entrega el picker.
Sin enumeración, o con el catálogo caído, devuelve "" y el turno sigue como
siempre (nunca bloquea).

La guarda solo cambia el FORMATO de la lista (2026-09-30, laboratorio 4567
t19): lo que el bot escribió antes de la lista encabeza el selector y lo que
escribió después lo cierra. Antes se perdía la pregunta del final, y el
laboratorio mostraba «el bot no envió nada».

Motor de decisiones (F5): si el texto es una lista para escoger lo decide la
capacidad `enumeracion` (regla de hoy, Jev en sombra o Jev con la regla de
respaldo, según el bot de la conversación).

DEHA: R-STATELESS / R-JSON (in str×2, out bool) / R-DIP (catálogo por
composition root, vault por `_append_intent`).
"""
from __future__ import annotations

from pathlib import Path

from temporalio import activity

from src.plugins.chats.agent.sales.catalog_scope import WHOLE_CATALOG
from src.plugins.chats.agent.sales.decisions.capabilities.texto import TextoCatalogo
from src.plugins.chats.agent.sales.decisions.guards import capability, decide_for_session
from src.plugins.chats.agent.sales.tools.ui_intents import (
    _append_intent,
    build_variant_picker_intent,
)
from src.plugins.chats.agent.sales.activities.flush_ui_intents import render_variant_picker_text
from src.plugins.chats.agent.sales.variant_enumeration import (
    as_lead_in,
    catalog_variant_labels,
    default_intro,
    intro_before,
    outro_after,
)
from src.sdk.connectorkit import get_catalog_client
from src.sdk.runtime import WORKSPACE_VAULT_DIR


async def _catalog_labels() -> tuple[list[str], list[str]]:
    result = await get_catalog_client().search(q="", limit=WHOLE_CATALOG)
    return catalog_variant_labels(result.results)


@activity.defn(name="apply_variant_enumeration_guard")
async def apply_variant_enumeration_guard_activity(session_id: str, final_text: str) -> bool | str:
    """El texto que va a recibir el cliente (el selector), o "" si el texto
    del bot sale como está. Histories anteriores al 2026-09-30 guardaron un
    bool (True = selector encolado): el tipo del resultado acepta los dos para
    que esos workflows se sigan reproduciendo, y el workflow lee cualquiera."""
    if not final_text or not final_text.strip():
        return ""
    try:
        aromas, colors = await _catalog_labels()
    except Exception as exc:  # noqa: BLE001 — catálogo caído: no bloquear el turno
        activity.logger.warning(
            "variant_enumeration_guard: catálogo no disponible (%s) — texto sin cambios",
            exc,
        )
        return ""
    # Motor de decisiones (F5): qué enumera el texto lo decide la capacidad
    # `enumeracion` con el proveedor del bot de la conversación (la regla de
    # hoy por defecto: idéntico a antes). Las etiquetas salen del catálogo.
    vault_dir = Path(WORKSPACE_VAULT_DIR)
    verdict = await decide_for_session(
        capability("enumeracion"),
        TextoCatalogo(text=final_text, aromas=tuple(aromas), colors=tuple(colors)),
        session_id=session_id,
        vault_dir=vault_dir,
    )
    if not verdict.value:
        return ""
    variant_type, labels = verdict.value[0], list(verdict.value[1])
    # Solo cambia el formato de la lista: lo que el bot dijo antes (la
    # respuesta, «Jengibre no está entre los aromas…») encabeza el selector y
    # lo que dijo después (su pregunta) lo cierra (laboratorio 4567 t19).
    intro = as_lead_in(intro_before(final_text, labels) or default_intro(variant_type))
    intent = build_variant_picker_intent(
        variant_type=variant_type,
        labels=labels,
        intro_text=intro,
        handle=None,
        closing_text=outro_after(final_text, labels) or None,
    )
    if intent is None:
        return ""
    _append_intent(session_id, intent)
    activity.logger.info(
        "variant_enumeration_guard: %d %s enumerados en texto → picker encolado (session=%s)",
        len(labels),
        variant_type,
        session_id,
    )
    return render_variant_picker_text(intent["params"]) or ""


__all__ = ("apply_variant_enumeration_guard_activity",)
