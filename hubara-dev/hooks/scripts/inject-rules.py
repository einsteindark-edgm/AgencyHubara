#!/usr/bin/env python3
"""SessionStart hook (plugin hubara-dev): inyecta la ley TDD + punteros del
harness en cada sesión del proyecto. Determinista, barato, siempre activo.

El conocimiento completo NO se inyecta acá (sería caro cada sesión): solo el
método no-negociable + dónde ir a buscar. La fuente de verdad vive en
ARCHITECTURE_FINAL_fable.md y en el skill hubara-plugin-developer.
"""
from __future__ import annotations

import json
import sys

CONTEXT = """## Harness hubara-dev activo — TDD obligatorio (rojo → verde → refactor)

No escribís una línea de código de producción sin un test que **falla primero**
y lo exige (las 3 leyes). Un rojo por `ImportError`/colección NO es rojo válido.
El test asierta **comportamiento observable**, no implementación. Pasos de
minutos, un comportamiento por vuelta.

- Método + harness por capa (dominio/activity/workflow/tool/entity/feature/gate):
  skill `hubara-plugin-developer` → `references/00-tdd-law.md`.
- Antes de editar, qué gate te frena: `references/01-hard-rules.md`.
- Verificación determinística: el comando `/hubara-gates` (o `references/03-command-panel.md`).
- Qué NO repetir (lecciones L-0..L-34): `references/04-lessons.md`.
- Motor de decisiones (L-34): cómo decide el bot (compra, baja, cortesía, zona,
  para quién es un texto…) son capacidades YAML + CEL de un paquete versionado.
  Un bug de decisión se arregla en una versión NUEVA del paquete (veredicto →
  ejemplo rojo DB010 → pregunta/umbral/fila), NUNCA con un `if`/regex en el
  lugar que decide; código solo en UN builtin genérico con su prueba.
  `references/06-decision-bundles.md` · `docs/motor-de-decisiones/index.html` ·
  certificar: `cd hubara_agency && uv run python -m src.sdk.cli decisions check`.
- Subagents: `hubara-explorer` (mapear antes de editar), `hubara-tdd-author`
  (escribir el test rojo), `hubara-gate-reviewer` (verificar antes de cerrar).

Si vas a programar en AgencyHubara y no tenés fresco el método, leé
`00-tdd-law.md` antes de tocar código."""


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
