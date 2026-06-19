#!/usr/bin/env python3
"""PreToolUse hook (plugin graphagents-dev): recordatorio TDD GUIADO al editar
código de producción de GraphAgents. NO bloquea (guiado + rojo/verde): inyecta
un recordatorio fuerte vía additionalContext y deja proceder. El bloqueo duro
frena refactors legítimos y la heurística test<->código es frágil; la red real
es el SessionStart (ley siempre activa) + el PostToolUse (corre el test) + el
Stop (corre los gates de cert/arquitectura).

Producción = un archivo `.py` bajo `GraphAgents/{sdk,graphs,tools}/` que NO sea
un test. Tests, manifests (YAML), fixtures, docs y configs no disparan el
recordatorio.
"""
from __future__ import annotations

import json
import re
import sys

_PROD = re.compile(r"/GraphAgents/(sdk|graphs|tools)/.*\.py$")
_TEST = re.compile(r"(/tests?/|/__tests__/|\.test\.|/test_|_test\.py$|conftest\.py$|/fixtures/)")


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
        sys.exit(0)  # input ilegible -> no estorbar

    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path", "") or ""

    if not _is_production(file_path):
        sys.exit(0)

    msg = (
        f"TDD (graphagents-dev): estás por editar producción ({file_path}). "
        "¿Ya tenés un test que FALLA y exige este cambio? Para una capability, ese "
        "rojo es un GOLDEN-REPLAY (fixture -> output exacto); para una tool, el "
        "decision payload; para el sdk/manifest, un check del TestKit. Si no lo "
        "tenés, pará y escribilo primero (o delegá en el subagent graph-tdd-author) "
        "— rojo -> verde -> refactor. Si es un refactor bajo tests verdes, seguí. "
        "Método: references/00-tdd-law.md · reglas: references/01-graph-rules.md."
    )
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
    sys.exit(0)


if __name__ == "__main__":
    main()
