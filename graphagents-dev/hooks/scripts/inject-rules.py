#!/usr/bin/env python3
"""SessionStart hook (plugin graphagents-dev): inyecta la ley del harness
GraphAgents (TDD + grafos deterministas + runtime durable) en cada sesión.
Determinista, barato, siempre activo.

El conocimiento completo NO se inyecta acá (sería caro): solo el método
no-negociable + dónde ir a buscar. La fuente de verdad vive en
GraphAgents/README.md y en el skill graphagents-developer.
"""
from __future__ import annotations

import json
import sys

CONTEXT = """## Harness graphagents-dev activo — TDD + grafos deterministas, runtime durable

`GraphAgents/` es un subsistema APARTE (no se fusiona con el monorepo): agentes
de análisis de datos (Meta Ads) con **LangGraph** (task graphs deterministas)
sobre el runtime durable **AgentSpan**, orquestados por **manifests YAML**.

Ley (re-implementada acá, igual de no-negociable que en el monorepo):
- **TDD rojo → verde → refactor.** El rojo de una capability es un GOLDEN-REPLAY
  que falla: dado un fixture de datos, el grafo debe producir EXACTAMENTE este
  output. Un rojo por `ImportError`/colección NO cuenta. El test asierta
  comportamiento observable (el output del grafo / la decisión de la tool).
- **G-DET:** el esqueleto del `StateGraph` es PURO; el LLM/IO va en nodos
  marcados. Si no podés replayear el grafo desde un fixture, falta aislar el LLM.
- **G-DUR:** toda acción outward (gasto, cambios en Meta) = `approval_required`
  / `@human_task`. Tools idempotentes.

Punteros (skill `graphagents-developer`):
- Método por capa (capability/tool/manifest/connector): `references/00-tdd-law.md`
- Reglas duras G-* y qué gate te frena: `references/01-graph-rules.md`
- Recetas (agregar manifest/grafo/tool/connector, componer supervisor): `references/02-recipes.md`
- Verificación determinística: `/graphagents-gates` (o `references/03-command-panel.md`)
- Lecciones L-#: `references/04-lessons.md`
- Subagents: `graph-explorer` (mapear antes de editar), `graph-tdd-author`
  (escribir el golden rojo), `graph-cert-reviewer` (certificar antes de cerrar).

Arquitectura completa del subsistema: `GraphAgents/README.md`. Si vas a tocar
GraphAgents/ y no tenés fresco el método, leé `00-tdd-law.md` antes de editar."""


def main() -> None:
    # Consumir stdin (payload del evento) aunque no lo usemos, para no romper el pipe.
    try:
        sys.stdin.read()
    except Exception:
        pass
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": CONTEXT,
                }
            }
        )
    )


if __name__ == "__main__":
    main()
