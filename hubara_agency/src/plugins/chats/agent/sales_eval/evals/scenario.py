"""Scenario del juez — builder COMPARTIDO entre el eval online y el golden (CI).

El Scenario del test case es el único canal conversation-level que el juez ve
(via `TurnParams.SCENARIO` en `metrics._geval`). Por ahí entran:

  * los HECHOS VERIFICADOS DEL SISTEMA (metadata escrita por el workflow/tools
    de borde — fix del falso negativo de `correct_handoff` en ep_010), y
  * el CATÁLOGO REAL (listas cerradas del snapshot — ground truth de
    `no_hallucination`; sin esto el juez no puede saber que "Melocotón" no
    existe ni que "14 aromas" es un conteo inventado).

PARIDAD DE CRITERIO: el harness online (`eval_activities`) y el golden del
GitHub Action (`scripts/golden_eval.py`) usan ESTE módulo — la misma
conversación debe juzgarse igual venga de prod o del set controlado. El golden
ya le pasa al juez los `tool_outputs` (capados a 1000 chars, donde el envelope
del search se trunca); el catálogo del Scenario cubre ese hueco con las listas
completas.

R-DIP: importa `platform/catalog` (plugin→platform OK) y módulos del propio
harness. Sin deepeval (strings puros).
"""
from __future__ import annotations

from typing import Any

from src.plugins.chats.agent.sales_eval.evals import reconstruct

BASE_SCENARIO = "Conversación real de ventas por WhatsApp (Hubara)."


async def catalog_ground_truth(catalog: Any | None = None) -> str:
    """Listas cerradas del catálogo REAL (snapshot local) para el juez.

    `catalog`: CatalogPort inyectable (tests); default el client del snapshot
    (`CATALOG_SNAPSHOT_DIR` — en el golden apunta al fixture committeado).
    Best-effort: catálogo caído → "" (el juez evalúa sin ground truth, como
    antes de esta mejora).
    """
    try:
        from src.platform.catalog import get_catalog_client, parse_variant_tags

        client = catalog if catalog is not None else get_catalog_client()
        result = await client.search(q="", limit=30)
        lines: list[str] = []
        for p in result.results:
            attrs = parse_variant_tags(p.tags)
            price = ""
            if p.variants and p.variants[0].prices:
                pr = p.variants[0].prices[0]
                price = f" — ${pr.amount} {pr.currency_code.upper()}"
            lines.append(
                f"• {p.title} (handle: {p.handle}){price}"
                f" — aromas ({len(attrs.aromas)}): {', '.join(attrs.aromas) or '(ninguno)'}"
                f" — colores ({len(attrs.colors)}): {', '.join(attrs.colors) or '(ninguno)'}"
            )
        if not lines:
            return ""
        return (
            "CATÁLOGO REAL VIGENTE (lista cerrada, snapshot local del sistema; "
            "puede diferir levemente del vigente al momento de la conversación):\n"
            + "\n".join(lines)
            + "\nTodo producto, precio, aroma, color o CONTEO de opciones que el "
            "asistente afirme y no esté en estas listas es invención."
        )
    except Exception:  # noqa: BLE001 — sin catálogo el juez evalúa como antes
        return ""


def build_scenario(
    episode: dict[str, Any] | None,
    metadata: dict[str, Any],
    *,
    episode_is_last: bool,
    catalog_block: str,
    base: str = BASE_SCENARIO,
) -> str:
    """Scenario del test case = contexto base + hechos del sistema + catálogo.

    Viaja al prompt del juez vía `TurnParams.SCENARIO` (ver `metrics._geval`).
    """
    parts = [base]
    facts = reconstruct.build_system_facts(
        episode, metadata, episode_is_last=episode_is_last
    )
    if facts:
        parts.append(
            "HECHOS VERIFICADOS DEL SISTEMA (registrados por el workflow "
            "runtime, NO generados por el LLM; las tool calls del cierre pueden "
            "no aparecer en el transcript — estos hechos prueban que ocurrieron):\n"
            + "\n".join(f"- {f}" for f in facts)
        )
    if catalog_block:
        parts.append(catalog_block)
    return "\n\n".join(parts)
