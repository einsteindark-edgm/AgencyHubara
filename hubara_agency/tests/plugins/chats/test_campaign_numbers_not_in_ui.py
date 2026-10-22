"""Las pantallas no escriben los números de una campaña (operador, 2026-10-22:
«todo esto solo debería aplicar a files de configuración por tipo de producto
y/o rules, y no debería ir código quemado»).

Desde cuántas cajas va el envío gratis lo dice el archivo de la campaña
(`envio_gratis_desde_cajas`). El dashboard y la app lo decían a mano, «(6
cajas o más)»: si el operador cambiaba el número en el YAML, las pantallas
seguían diciendo 6. La tarjeta del cliente sí lo toma del archivo
(`FreeShipping.label`).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
_UI = [
    *(ROOT / "frontend_dashboard" / "src").rglob("*.ts"),
    *(ROOT / "frontend_dashboard" / "src").rglob("*.tsx"),
    *(ROOT / "android_operator" / "screens").glob("*.json"),
]
#: «6 cajas o más», «desde 6 cajas»: un umbral de campaña escrito a mano.
_THRESHOLD = re.compile(r"\b\d+\s+cajas\s+o\s+m[aá]s|\bdesde\s+\d+\s+cajas", re.IGNORECASE)


@pytest.mark.skipif(not (ROOT / "frontend_dashboard" / "src").is_dir(), reason="sin el dashboard en este clon")
def test_no_screen_writes_the_free_shipping_threshold_of_a_campaign() -> None:
    found = [
        f"{path.relative_to(ROOT)}:{n}"
        for path in _UI
        if ".test." not in path.name
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if _THRESHOLD.search(line)
    ]

    assert found == [], found
