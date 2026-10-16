"""Una decisión nueva nace en una versión del paquete (incidente 2026-10-13).

Las versiones publicadas no se editan (su huella está congelada) y tienen que
seguir certificando en el motor de hoy: si el catálogo pide una capacidad
nueva en `capabilities:`, todas las versiones anteriores dejan de certificar
(DB003). `capabilities_since: {nombre: versión}` la pide desde esa versión:
las anteriores corren sin ella y, desde ahí, falta = DB003 como cualquiera.
"""
from __future__ import annotations

from pathlib import Path

from src.platform.decisions import check_bundle, load_bundle
from tests.platform.decisions.test_bundle_checker import BAJA, CATALOG, _write

_PLACES = {"herramientas": "Dentro de las herramientas del bot"}
_ABOUT = {
    "baja": {"name": "Baja de mensajes", "where": ["herramientas"], "solves": "Reconoce la baja."},
    "nueva": {"name": "Decisión nueva", "where": ["herramientas"], "solves": "Lo que resuelve la nueva."},
}
SINCE_CATALOG = {
    **CATALOG,
    "capabilities": ["baja"],
    "capabilities_since": {"nueva": 7},
    "places": _PLACES,
    "about": _ABOUT,
}
NUEVA = {**BAJA, "capability": "nueva"}


def _codes(tmp_path: Path, capabilities: dict, *, version: int, catalog: dict | None = None) -> list[str]:
    bundle_dir, catalog_path = _write(tmp_path, capabilities, bundle={"version": version}, catalog=catalog or SINCE_CATALOG)
    return [str(d) for d in check_bundle(bundle_dir, catalog_path)]


def test_a_version_published_before_the_decision_existed_still_certifies_without_it(tmp_path: Path) -> None:
    assert _codes(tmp_path, {"baja": BAJA}, version=6) == []


def test_from_its_version_on_a_bundle_without_the_decision_does_not_certify(tmp_path: Path) -> None:
    codes = _codes(tmp_path, {"baja": BAJA}, version=7)

    assert any(c.startswith("DB003 bundle.yaml: capabilities") and "nueva" in c for c in codes), codes


def test_a_bundle_from_its_version_on_brings_it_and_carries_what_it_solves(tmp_path: Path) -> None:
    bundle_dir, catalog_path = _write(
        tmp_path, {"baja": BAJA, "nueva": NUEVA}, bundle={"version": 7}, catalog=SINCE_CATALOG
    )

    bundle = load_bundle(bundle_dir, catalog_path)

    assert set(bundle.capabilities) == {"baja", "nueva"}
    assert bundle.about["nueva"].solves == "Lo que resuelve la nueva."


def test_the_new_decision_also_needs_its_explanation(tmp_path: Path) -> None:
    catalog = {**SINCE_CATALOG, "about": {"baja": _ABOUT["baja"]}}

    codes = _codes(tmp_path, {"baja": BAJA}, version=6, catalog=catalog)

    assert any(c.startswith("DB003 builtins.yaml: about") and "nueva" in c for c in codes), codes


def test_a_decision_cannot_be_asked_for_always_and_since_a_version(tmp_path: Path) -> None:
    catalog = {**SINCE_CATALOG, "capabilities": ["baja", "nueva"]}

    codes = _codes(tmp_path, {"baja": BAJA, "nueva": NUEVA}, version=7, catalog=catalog)

    assert any(c.startswith("DB003 builtins.yaml: capabilities_since") and "nueva" in c for c in codes), codes


def test_the_compiled_bundle_says_which_decisions_come_after_its_version(tmp_path: Path) -> None:
    """El lugar que pide una decisión nueva le pregunta al paquete (no relee el
    catálogo): las que nacen después de su versión no se corren con él."""
    old = load_bundle(*_write(tmp_path / "v6", {"baja": BAJA}, bundle={"version": 6}, catalog=SINCE_CATALOG))
    new = load_bundle(*_write(tmp_path / "v7", {"baja": BAJA, "nueva": NUEVA}, bundle={"version": 7},
                              catalog=SINCE_CATALOG))

    assert old.later == frozenset({"nueva"})
    assert new.later == frozenset()
