"""Los wamids del doble check nunca van detrás de `workflow.patched`.

Caso 2026-10-15 (3.er toque del remarketing «sin confirmación»): Temporal
recuerda el PRIMER resultado de `workflow.patched(id)` para todo el run. En un
run que venía de antes del deploy, ese primer resultado es el False de
reproducir un mensaje viejo, y todos los mensajes nuevos del run se guardaban
sin wamids. Pasar las burbujas es solo el contenido de la activity (L-22): no
necesita patch. `deprecate_patch` queda para reproducir los runs que ya
grabaron la marca.
"""
from __future__ import annotations

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
_GATE = re.compile(r"""workflow\.patched\(\s*["']persist-wamids-v1["']\s*\)""")


def test_no_workflow_gates_the_wamids_behind_patched() -> None:
    offenders = [
        f"{path.relative_to(_SRC.parent)}:{n}"
        for path in sorted(_SRC.rglob("*.py"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _GATE.search(line)
    ]

    assert offenders == [], (
        "`workflow.patched('persist-wamids-v1')` deja sin wamids a los mensajes "
        "nuevos de un run que venía de antes del deploy; usa deprecate_patch y "
        "pasa las burbujas siempre: " + ", ".join(offenders)
    )
