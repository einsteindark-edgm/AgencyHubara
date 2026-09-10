"""D2.2 — estado del sync con Meta por agente, en el vault
(``<vault>/_mba/sync/<agent_id>.json``, escritura atómica del SDK).

Guarda qué ids creamos en Meta (para borrar SOLO lo nuestro), el hash de lo
último enviado (``never_say_phrases`` es write-only) y el resultado del
último apply / intento, que la tab muestra.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Callable

from src.sdk.runtime import atomic_write_json

__all__ = ["SyncStateStore"]

_AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
#: Lock por archivo (por proceso): dos use cases (sync D2.2, rollout D2.3)
#: escriben el mismo json; ``update`` hace read-modify-write sin intercalarse.
_FILE_LOCKS: dict[str, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


class SyncStateStore:
    def __init__(self, vault_dir: str | Path) -> None:
        self._dir = Path(vault_dir) / "_mba" / "sync"

    def _path(self, agent_id: str) -> Path:
        if not _AGENT_ID.fullmatch(agent_id):
            raise ValueError(f"agent_id inválido: {agent_id!r}")
        return self._dir / f"{agent_id}.json"

    def read(self, agent_id: str) -> dict[str, Any]:
        path = self._path(agent_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def write(self, agent_id: str, data: dict[str, Any]) -> None:
        path = self._path(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, data)

    def update(self, agent_id: str, mutator: Callable[[dict[str, Any]], dict[str, Any] | None]) -> dict[str, Any]:
        """Read-modify-write atómico sobre el estado FRESCO del disco: el
        mutator recibe lo que hay ahora y devuelve el estado nuevo (``None``
        = no escribir). Es la única forma correcta de tocar el archivo
        después de haber esperado a Meta: un estado leído antes del
        roundtrip puede pisar lo que otro use case escribió entre medio."""
        path = self._path(agent_id)
        with _FILE_LOCKS_GUARD:
            lock = _FILE_LOCKS.setdefault(str(path), threading.Lock())
        with lock:
            current = self.read(agent_id)
            new = mutator(dict(current))
            if new is None:
                return current
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, new)
            return new
