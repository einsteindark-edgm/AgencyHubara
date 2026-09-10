"""D2.2 — estado del sync con Meta por agente, en el vault
(``<vault>/_mba/sync/<agent_id>.json``, escritura atómica del SDK).

Guarda qué ids creamos en Meta (para borrar SOLO lo nuestro), el hash de lo
último enviado (``never_say_phrases`` es write-only) y el resultado del
último apply / intento, que la tab muestra.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.sdk.runtime import atomic_write_json

__all__ = ["SyncStateStore"]

_AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


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
