"""Loader del YAML de familias de color — configurable POR TENANT.

  * Path por defecto: `hubara_agency/config/color_families/families.yaml`
    (viaja en la imagen: el Dockerfile copia `hubara_agency/` entero).
  * Override por tenant: env `COLOR_FAMILIES_PATH=/ruta/families.yaml`
    (tenants/<tenant>.env / SSM). Un tenant que distingue "Beige" de
    "Blanco" separa la familia en SU archivo sin tocar código.
  * Hot-reload por mtime (patrón `customer_scoring/loader.py`): editar el
    YAML se aplica al siguiente `set_order_slot`, sin redeploy.
  * Degrada ABIERTO: sin archivo → familias vacías (el matcheo vuelve a ser
    exacto, comportamiento legacy) + WARNING. YAML corrupto tras un edit →
    conserva la última versión válida + WARNING. Nunca tumba la tool.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import yaml

from src.platform.catalog.color_families import (
    EMPTY_COLOR_FAMILIES,
    ColorFamilies,
    InvalidColorFamiliesError,
    parse_color_families,
)

log = logging.getLogger(__name__)

COLOR_FAMILIES_PATH_ENV = "COLOR_FAMILIES_PATH"
DEFAULT_COLOR_FAMILIES_PATH: Path = (
    Path(__file__).resolve().parents[3] / "config" / "color_families" / "families.yaml"
)


def resolve_color_families_path() -> Path:
    """Path efectivo: env del tenant si está seteado, si no el default."""
    override = os.getenv(COLOR_FAMILIES_PATH_ENV, "").strip()
    return Path(override).expanduser() if override else DEFAULT_COLOR_FAMILIES_PATH


class ColorFamiliesLoader:
    """Lee + valida + cachea el YAML con hot-reload por mtime (thread-safe)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._cached: ColorFamilies | None = None
        self._cached_mtime: float = -1.0
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> ColorFamilies:
        with self._lock:
            return self._load_locked()

    def _load_locked(self) -> ColorFamilies:
        try:
            mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            if self._cached is None:
                log.warning(
                    "color_families: %s no existe — matcheo de color EXACTO "
                    "(sin tolerancia de gama) hasta que aparezca el archivo",
                    self._path,
                )
                self._cached = EMPTY_COLOR_FAMILIES
                self._cached_mtime = -1.0
            return self._cached
        except OSError as exc:
            log.warning("color_families: stat de %s falló (%s)", self._path, exc)
            return self._cached or EMPTY_COLOR_FAMILIES

        if self._cached is not None and mtime == self._cached_mtime:
            return self._cached

        try:
            with self._path.open("r", encoding="utf-8") as fh:
                doc = yaml.safe_load(fh)
            parsed = parse_color_families(doc)
        except (OSError, yaml.YAMLError, InvalidColorFamiliesError) as exc:
            if self._cached is not None and self._cached_mtime >= 0:
                log.warning(
                    "color_families: %s inválido tras edit (%s) — se conserva "
                    "la última versión válida (v%d)",
                    self._path, exc, self._cached.version,
                )
                return self._cached
            log.warning(
                "color_families: %s inválido (%s) — matcheo de color EXACTO",
                self._path, exc,
            )
            self._cached = EMPTY_COLOR_FAMILIES
            self._cached_mtime = mtime
            return self._cached

        self._cached = parsed
        self._cached_mtime = mtime
        log.info(
            "color_families: cargado %s (v%d, %d familias)",
            self._path, parsed.version, len(parsed.families),
        )
        return parsed


_loaders: dict[Path, ColorFamiliesLoader] = {}
_loaders_lock = threading.Lock()


def get_color_families_loader(path: Path | None = None) -> ColorFamiliesLoader:
    """Loader compartido por path (uno por proceso; el cache vive adentro)."""
    target = path or resolve_color_families_path()
    with _loaders_lock:
        loader = _loaders.get(target)
        if loader is None:
            loader = ColorFamiliesLoader(target)
            _loaders[target] = loader
        return loader


def get_color_families() -> ColorFamilies:
    """Familias vigentes para este tenant (hot-reload incluido)."""
    return get_color_families_loader().load()
