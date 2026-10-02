"""`uv run python -m src.sdk.cli decisions …`: el certificador de paquetes de
decisión como comando (PAQUETES_DE_DECISION.md §6).

`decisions check` sale con 0 si el paquete compila y con 1 (y cada error con
su código y ruta) si no; `decisions schema` escribe el esquema JSON que usa
el editor para autocompletar y marcar errores mientras se escribe.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.sdk.cli import main
from tests.platform.decisions.test_bundle_checker import BAJA, _baja, _write


def test_check_passes_a_valid_bundle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle_dir, catalog_path = _write(tmp_path, {"baja": BAJA})

    code = main(["decisions", "check", str(bundle_dir), "--catalog", str(catalog_path)])

    assert code == 0
    assert "OK tienda-ventas@1 (1 capacidad)" in capsys.readouterr().out


def test_check_fails_with_each_error_and_where(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle_dir, catalog_path = _write(
        tmp_path, {"baja": _baja(decide=[{"when": "p['baja.pid'] >= th['yes']", "then": True}, {"otherwise": "doubt"}])}
    )

    code = main(["decisions", "check", str(bundle_dir), "--catalog", str(catalog_path)])

    out = capsys.readouterr().out
    assert code == 1
    assert "DB005 capabilities/baja.yaml: decide[0].when" in out
    assert "FALLA tienda-ventas" in out


def test_the_catalog_defaults_to_the_one_next_to_the_bundles(tmp_path: Path) -> None:
    bundle_dir, _catalog = _write(tmp_path, {"baja": BAJA})  # builtins.yaml junto a la carpeta del paquete

    assert main(["decisions", "check", str(bundle_dir)]) == 0


def test_schema_writes_the_editor_schemas(tmp_path: Path) -> None:
    assert main(["decisions", "schema", "--out", str(tmp_path)]) == 0

    capability = json.loads((tmp_path / "decision-capability.schema.json").read_text(encoding="utf-8"))
    bundle = json.loads((tmp_path / "decision-bundle.schema.json").read_text(encoding="utf-8"))
    assert {"capability", "decide", "examples", "questions"} <= set(capability["properties"])
    assert capability["additionalProperties"] is False
    assert {"id", "version", "engine_contract"} <= set(bundle["properties"])


def test_the_committed_editor_schemas_are_up_to_date(tmp_path: Path) -> None:
    """Los YAML del repo apuntan a `hubara_agency/schemas/` (yaml-language-server):
    si un modelo cambia y el esquema no, el editor autocompleta mal."""
    committed = Path(__file__).resolve().parents[2] / "schemas"
    main(["decisions", "schema", "--out", str(tmp_path)])

    for name in ("decision-capability.schema.json", "decision-bundle.schema.json"):
        assert (committed / name).read_text(encoding="utf-8") == (tmp_path / name).read_text(encoding="utf-8"), (
            f"{name} desactualizado: cd hubara_agency && uv run python -m src.sdk.cli decisions schema"
        )
