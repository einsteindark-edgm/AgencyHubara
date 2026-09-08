"""El YAML que viaja en la imagen (`config/color_families/families.yaml`) es
válido y cubre los colores REALES del catálogo Hubara; el loader hace
hot-reload y es configurable por tenant vía `COLOR_FAMILIES_PATH`."""
from __future__ import annotations

import os
import time
from pathlib import Path

from src.platform.catalog.color_families import family_of_color, resolve_color_family
from src.platform.catalog.color_families_loader import (
    COLOR_FAMILIES_PATH_ENV,
    DEFAULT_COLOR_FAMILIES_PATH,
    ColorFamiliesLoader,
    get_color_families,
    resolve_color_families_path,
)

# Tags de color vistos en el catálogo real (snapshot + fixtures).
_HUBARA_CATALOG_COLORS = [
    "Blanco", "gris", "Rosado", "Morado", "Lila", "Azul", "Amarillo",
    "verde", "Naranja", "rojo", "negro", "café",
]


def test_shipped_config_loads_and_covers_hubara_catalog_colors():
    fam = ColorFamiliesLoader(DEFAULT_COLOR_FAMILIES_PATH).load()
    assert not fam.is_empty
    missing = [c for c in _HUBARA_CATALOG_COLORS if family_of_color(c, fam) is None]
    assert missing == []


def test_shipped_config_has_no_shade_in_two_families():
    fam = ColorFamiliesLoader(DEFAULT_COLOR_FAMILIES_PATH).load()
    seen: dict[str, str] = {}
    dupes = []
    for family in fam.families:
        for shade in family.shades:
            key = shade.casefold()
            if key in seen and seen[key] != family.id:
                dupes.append((shade, seen[key], family.id))
            seen.setdefault(key, family.id)
    assert dupes == []


def test_shipped_config_resolves_operator_examples():
    fam = ColorFamiliesLoader(DEFAULT_COLOR_FAMILIES_PATH).load()
    catalog = ["Blanco", "gris", "Rosado", "Morado", "Lila", "Azul"]
    for asked in ("azul mar", "azul clarito", "celeste", "azul marino"):
        res = resolve_color_family(asked, catalog, fam)
        assert res is not None and res.canonical == "Azul", asked
    assert resolve_color_family("lavanda", catalog, fam).canonical == "Lila"
    assert resolve_color_family("violeta", catalog, fam).canonical == "Morado"
    assert resolve_color_family("fucsia", catalog, fam).canonical == "Rosado"
    assert resolve_color_family("plomo", catalog, fam).canonical == "gris"
    assert resolve_color_family("hueso", catalog, fam).canonical == "Blanco"


def test_loader_missing_file_degrades_to_empty(tmp_path: Path):
    fam = ColorFamiliesLoader(tmp_path / "nope.yaml").load()
    assert fam.is_empty


def test_loader_hot_reloads_and_keeps_last_valid_on_corrupt_edit(tmp_path: Path):
    path = tmp_path / "families.yaml"
    path.write_text(
        "version: 1\nfamilies:\n  azul: {label: Azul, shades: [azul, celeste]}\n",
        encoding="utf-8",
    )
    loader = ColorFamiliesLoader(path)
    assert family_of_color("celeste", loader.load()).id == "azul"

    path.write_text(
        "version: 2\nfamilies:\n  verde: {label: Verde, shades: [verde, menta]}\n",
        encoding="utf-8",
    )
    os.utime(path, (time.time() + 5, time.time() + 5))  # fuerza mtime distinto
    fam2 = loader.load()
    assert fam2.version == 2
    assert family_of_color("menta", fam2).id == "verde"

    path.write_text("families: [esto no es un mapping]\n", encoding="utf-8")
    os.utime(path, (time.time() + 10, time.time() + 10))
    fam3 = loader.load()
    assert fam3.version == 2  # conserva la última válida


def test_tenant_override_via_env(tmp_path: Path, monkeypatch):
    custom = tmp_path / "tenant.yaml"
    custom.write_text(
        "version: 7\nfamilies:\n  beige: {label: Beige, shades: [beige, arena]}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(COLOR_FAMILIES_PATH_ENV, str(custom))
    assert resolve_color_families_path() == custom
    fam = ColorFamiliesLoader(resolve_color_families_path()).load()
    assert fam.version == 7
    assert family_of_color("arena", fam).id == "beige"

    monkeypatch.delenv(COLOR_FAMILIES_PATH_ENV)
    assert resolve_color_families_path() == DEFAULT_COLOR_FAMILIES_PATH


def test_get_color_families_returns_shipped_config_by_default(monkeypatch):
    monkeypatch.delenv(COLOR_FAMILIES_PATH_ENV, raising=False)
    fam = get_color_families()
    assert family_of_color("Azul", fam).id == "azul"
