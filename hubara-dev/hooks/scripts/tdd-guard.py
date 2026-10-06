#!/usr/bin/env python3
"""PreToolUse hook (plugin hubara-dev): recordatorio TDD GUIADO al editar código
de producción. NO bloquea (el usuario eligió "guiado + rojo/verde"): inyecta un
recordatorio fuerte vía additionalContext y deja proceder. El bloqueo duro
frena refactors legítimos y la heurística test↔código es frágil; la red real
es el SessionStart (ley siempre activa) + el PostToolUse (corre el test).

Producción = un archivo bajo `src/` de hubara_agency o frontend_dashboard que
NO sea un test. Tests, docs, configs, manifests no disparan el recordatorio.

Un LUGAR QUE DECIDE (el ingest, las tools, el egreso, remarketing, el cierre…
de ventas) suma el recordatorio del motor de decisiones (L-34): si el cambio es
de cómo decide el bot, va en una versión nueva del paquete, no en un `if` acá.
Un YAML de `decisions/bundles/` recuerda que una versión publicada no se edita.
"""
from __future__ import annotations

import json
import re
import sys

_PROD = re.compile(r"/(hubara_agency|frontend_dashboard)/src/")
_TEST = re.compile(r"(/tests?/|/__tests__/|\.test\.|\.spec\.|/test_|_test\.py$|\.arch\.test\.)")
# Lugares que piden decisiones al motor (`capability("x")`): PAQUETES_DE_DECISION.md §10.1.
_DECISION_PLACE = re.compile(
    r"/hubara_agency/src/plugins/chats/agent/(?:"
    r"sales/(?:decisions/(?:readings|guards|egress|egress_activities|remarketing_context|contact|cierre)\.py"
    r"|tools/[^/]+\.py"
    r"|activities/(?:build_prompt_stage|variant_enumeration_guard|turn_trace)\.py"
    r"|use_cases/ingest_inbound_message\.py)"
    r"|remarketing/.+\.py)$"
)
_BUNDLE = re.compile(r"/decisions/bundles/.+\.ya?ml$")

_DECISION_MSG = (
    "Motor de decisiones (L-34): este archivo es un LUGAR QUE DECIDE. Si el cambio "
    "es de cómo decide el bot (compra, baja, cortesía, acuse, zona, para quién es "
    "el texto…), NO va acá como if/regex/lista de palabras/umbral: va en una "
    "versión nueva del paquete (ejemplo rojo DB010 → pregunta/umbral/fila). "
    "Triage por veredicto: references/06-decision-bundles.md."
)
_BUNDLE_MSG = (
    "Paquete de decisión: una versión publicada NO se edita (huella en "
    "test_decision_bundles_published.py) — copiá la carpeta a <id>-N con version + 1. "
    "El rojo es un `examples:` que da DB010 en `decisions check`."
)


def _is_production(file_path: str) -> bool:
    if not file_path:
        return False
    if _TEST.search(file_path):
        return False
    return bool(_PROD.search(file_path))


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # input ilegible → no estorbar

    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path", "") or ""

    if _BUNDLE.search(file_path):
        _emit(_BUNDLE_MSG)
        sys.exit(0)
    if not _is_production(file_path):
        sys.exit(0)

    msg = (
        f"TDD (hubara-dev): estás por editar código de producción ({file_path}). "
        "¿Ya tenés un test que FALLA y exige este cambio? Si no, pará y escribilo "
        "primero (o delegá en el subagent hubara-tdd-author) — rojo → verde → "
        "refactor. Si esto es un refactor bajo tests verdes, seguí. "
        "Método: references/00-tdd-law.md."
    )
    if _DECISION_PLACE.search(file_path):
        msg = f"{msg} {_DECISION_MSG}"
    _emit(msg)
    sys.exit(0)


def _emit(msg: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": msg,
                }
            }
        )
    )


if __name__ == "__main__":
    main()
