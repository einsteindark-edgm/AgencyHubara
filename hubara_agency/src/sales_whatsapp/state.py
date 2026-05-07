"""Per-session state adapters (filesystem) del dominio Sales.

Agrupa los adapters de filesystem que persisten el estado de cada sesion bajo
``<vault>/<session_id>/``:

  * ``FilesystemMessageHistoryStore`` — append-only del JSONL de eventos del
    usuario en ``<vault>/<session_id>/sessions/<session_id>.jsonl``.
  * ``FilesystemMetadataStore`` — lectura/escritura del documento por sesion en
    ``<vault>/<session_id>/metadata.json``.

Los use cases (``ingest_inbound_message``, ``load_or_start_sales_session``) y
las tools (``routing``, ``tags``) los importan **directo** — a esta escala
(1.3K LoC, dos adapters concretos sin variantes) un Protocol intermedio en
``application/ports/`` solo agrega indireccion sin aportar testabilidad
(los fakes en tests siguen pasando duck-typed: la "abstraccion" es la firma
publica de las clases).

Tipos compatibles con tests con fakes: cualquier objeto que implemente
``read(session_id) -> dict`` / ``write(session_id, data) -> None`` /
``append_user_event(session_id, content) -> None`` se puede inyectar en los
use cases via constructor sin tocar isinstance checks (Python es duck-typed,
PR-E elimina los `Protocol` redundantes).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class FilesystemMessageHistoryStore:
    """Adapter filesystem del log append-only de eventos del usuario.

    Cada sesion mapea a ``<vault_dir>/<session_id>/sessions/<session_id>.jsonl``.
    Cada llamada a ``append_user_event`` agrega una linea JSON serializada con
    ``ensure_ascii=False`` (mismo shape que el legado en ``service.py``).
    """

    def __init__(self, vault_dir: Path) -> None:
        self._vault_dir = vault_dir

    def _path_for(self, session_id: str) -> Path:
        return self._vault_dir / session_id / "sessions" / f"{session_id}.jsonl"

    def append_user_event(self, session_id: str, content: str) -> None:
        path = self._path_for(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {"role": "user", "content": content}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")


class FilesystemMetadataStore:
    """Adapter filesystem del documento de metadatos por sesion.

    Cada sesion mapea a ``<vault_dir>/<session_id>/metadata.json``. La lectura
    es tolerante a archivos corruptos (retorna ``{}`` ante ``JSONDecodeError``);
    la escritura es atomica por simple ``write_text`` (mismo comportamiento que
    el legado en ``service.py``).
    """

    def __init__(self, vault_dir: Path) -> None:
        self._vault_dir = vault_dir

    def _path_for(self, session_id: str) -> Path:
        return self._vault_dir / session_id / "metadata.json"

    def read(self, session_id: str) -> dict[str, Any]:
        path = self._path_for(session_id)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return data

    def write(self, session_id: str, data: dict[str, Any]) -> None:
        path = self._path_for(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
