"""Hot-reload loader del `rules.yaml`.

Patrón mirror de `platform/catalog/local_snapshot.py`:
  * Cache invalidation por mtime → cero overhead si el file no cambió.
  * Validación al cargar; si el nuevo file es inválido, mantiene el último
    válido y loguea WARNING (no rompe la app).
  * Thread-safe via `threading.Lock` (FastAPI puede llamar concurrente).

R-STATELESS: el loader instance NO es module-level singleton acá. La
composition factory en `composition.py` lo expone via `@lru_cache(maxsize=1)`.

R-DET: el caller pasa `now_ms` para el score; el loader solo controla la
versión de rules — la determinación del score sigue siendo pura.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import yaml

from src.platform.customer_scoring.rules import (
    InvalidRulesDocError,
    RulesDoc,
    parse_rules_doc,
)

log = logging.getLogger(__name__)


class RulesUnavailableError(RuntimeError):
    """No hay rules cargadas (el file no existe o el primer parse falló)."""


class YamlRulesLoader:
    """Lee + valida + cachea `rules.yaml` con hot-reload por mtime.

    Uso:
        loader = YamlRulesLoader(Path("config/customer_scoring/rules.yaml"))
        doc = loader.load()  # devuelve RulesDoc, cachea hasta mtime cambie

    Si el file no existe al primer load → `RulesUnavailableError`.
    Si una reload falla (YAML corrupto / schema inválido) → loguea WARNING y
    devuelve el doc cacheado anterior. Solo al FIRST load es fatal.
    """

    def __init__(self, rules_path: Path) -> None:
        self._path = rules_path
        self._cached_doc: RulesDoc | None = None
        self._cached_mtime: float = 0.0
        self._lock = threading.Lock()

    def load(self) -> RulesDoc:
        """Devuelve el `RulesDoc` actual. Recarga si el file cambió desde
        el último load.
        """
        if not self._path.exists():
            if self._cached_doc is not None:
                # Edge: el file fue borrado pero ya teníamos doc cacheado.
                # Conservamos el último válido y warning.
                log.warning(
                    "rules.yaml desapareció en %s — usando última versión "
                    "cacheada (v%d)",
                    self._path, self._cached_doc.version,
                )
                return self._cached_doc
            raise RulesUnavailableError(
                f"rules.yaml no existe en {self._path}. Crear el file o "
                f"setear el path correcto en composition."
            )

        try:
            mtime = self._path.stat().st_mtime
        except OSError as exc:
            if self._cached_doc is not None:
                log.warning("rules.yaml stat falló (%s) — usando cache", exc)
                return self._cached_doc
            raise RulesUnavailableError(f"rules.yaml stat falló: {exc}") from exc

        with self._lock:
            if self._cached_doc is not None and mtime <= self._cached_mtime:
                return self._cached_doc

            try:
                raw_text = self._path.read_text(encoding="utf-8")
                raw_dict = yaml.safe_load(raw_text)
                doc = parse_rules_doc(raw_dict)
            except (OSError, yaml.YAMLError, InvalidRulesDocError) as exc:
                if self._cached_doc is not None:
                    log.warning(
                        "rules.yaml reload falló (%s: %s) — manteniendo "
                        "versión cacheada v%d",
                        type(exc).__name__, exc, self._cached_doc.version,
                    )
                    return self._cached_doc
                raise RulesUnavailableError(
                    f"rules.yaml inválido en first load: {type(exc).__name__}: {exc}"
                ) from exc

            self._cached_doc = doc
            self._cached_mtime = mtime
            log.info(
                "rules.yaml cargado v%d (mtime=%.0f) — %d tags, %d weights",
                doc.version, mtime, len(doc.tags), len(doc.score_weights),
            )
            return doc


def load_rules_from_dict(raw: dict[str, Any]) -> RulesDoc:
    """Helper para tests — bypass del filesystem cargando directo de un dict
    in-memory."""
    return parse_rules_doc(raw)
