"""P-27 — todo plugin instancia el TCK ("sin tests de arquitectura no compila").

El gate central de F-SDK-2: por cada manifest descubierto en el repo DEBE
existir ``tests/conformance/test_<id>_conformance.py`` invocando
``conformance_suite("<id>")``. Es la emulación del compilador para un
lenguaje no compilado: el artefacto de conformance es PARTE del artefacto de
código — un plugin nuevo sin su TCK pone CI en rojo y no mergea.

Nota de diseño: el archivo del plugin son 3 líneas (instancia, no copia) —
los checks viven en ``src/sdk/testkit/`` y un check nuevo upgradea a toda la
flota sin tocar estos archivos (L-3: nada de listas/copias a mano).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.platform.plugin_manifest import all_manifests

CONFORMANCE_DIR = Path(__file__).resolve().parents[1] / "conformance"


@pytest.mark.parametrize("plugin_id", [pid for pid, _ in all_manifests()])
def test_p27_plugin_instantiates_tck(plugin_id: str) -> None:
    path = CONFORMANCE_DIR / f"test_{plugin_id}_conformance.py"
    assert path.exists(), (
        f"[P-27] el plugin {plugin_id!r} no instancia el TCK — falta "
        f"{path.relative_to(CONFORMANCE_DIR.parents[1])}. Fix (3 líneas):\n"
        f'  from src.sdk.testkit import conformance_suite\n'
        f'  globals().update(conformance_suite("{plugin_id}"))\n'
        f"(docs/_sdk/04-testkit-certificacion.md)"
    )
    text = path.read_text(encoding="utf-8")
    assert re.search(
        rf"conformance_suite\(\s*['\"]{plugin_id}['\"]\s*\)", text
    ), (
        f"[P-27] {path.name} existe pero no invoca "
        f'conformance_suite("{plugin_id}") — el TCK debe instanciarse con el '
        f"id PROPIO del plugin"
    )


def test_p27_no_orphan_conformance_files() -> None:
    """Simetría: un TCK sin plugin es un zombie (plugin renombrado/borrado)."""
    plugin_ids = {pid for pid, _ in all_manifests()}
    orphans = sorted(
        p.name
        for p in CONFORMANCE_DIR.glob("test_*_conformance.py")
        if re.fullmatch(r"test_(.+)_conformance\.py", p.name).group(1) not in plugin_ids
    )
    assert not orphans, (
        f"[P-27] archivos TCK sin plugin correspondiente (borralos o restaurá "
        f"el manifest): {orphans}"
    )
