"""Gate de arquitectura: TODO manifest valida el schema y declara archetype.
Incluye el caso NEGATIVO (el gate que nunca falla es un gate roto)."""
from __future__ import annotations

from pathlib import Path

import pytest

from sdk.manifest_model import TaskGraphManifest, load_manifest

GA = Path(__file__).resolve().parents[2]
MANIFESTS = sorted((GA / "manifests").glob("*.yaml"))


def test_hay_manifests() -> None:
    assert MANIFESTS, "no se encontraron manifests/*.yaml"


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.name)
def test_manifest_valida_y_declara_archetype(path: Path) -> None:
    m = load_manifest(path)
    assert isinstance(m, TaskGraphManifest)
    assert m.archetype in {"extractor", "analyzer", "reporter", "supervisor"}
    assert m.name


def test_schema_rechaza_manifest_invalido(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: Bad_Name\narchetype: wizard\n", encoding="utf-8")
    with pytest.raises(Exception):
        load_manifest(bad)
