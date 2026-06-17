#!/usr/bin/env python3
"""PreToolUse hook (plugin hubara-dev): recordatorio TDD GUIADO al editar código
de producción. NO bloquea (el usuario eligió "guiado + rojo/verde"): inyecta un
recordatorio fuerte vía additionalContext y deja proceder. El bloqueo duro
frena refactors legítimos y la heurística test↔código es frágil; la red real
es el SessionStart (ley siempre activa) + el PostToolUse (corre el test).

Producción = un archivo bajo `src/` de hubara_agency o frontend_dashboard que
NO sea un test. Tests, docs, configs, manifests no disparan el recordatorio.
"""
from __future__ import annotations

import json
import re
import sys

_PROD = re.compile(r"/(hubara_agency|frontend_dashboard)/src/")
_TEST = re.compile(r"(/tests?/|/__tests__/|\.test\.|\.spec\.|/test_|_test\.py$|\.arch\.test\.)")


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

    if not _is_production(file_path):
        sys.exit(0)

    msg = (
        f"TDD (hubara-dev): estás por editar código de producción ({file_path}). "
        "¿Ya tenés un test que FALLA y exige este cambio? Si no, pará y escribilo "
        "primero (o delegá en el subagent hubara-tdd-author) — rojo → verde → "
        "refactor. Si esto es un refactor bajo tests verdes, seguí. "
        "Método: references/00-tdd-law.md."
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
