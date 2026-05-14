"""Cross-component state adapters de filesystem.

Aqui viven los adapters que tocan `metadata.json` per-sesion. La canonical
location es `platform/` porque MULTIPLES componentes lo consumen:

  * `sales_whatsapp/use_cases/load_or_start_sales_session.py` (routing).
  * `sales_whatsapp/composition.py` (DI).
  * `dashboard/handoff.py` (intervene + return-to-bot leen/escriben metadata).

Regla DEHA multi-agent: el estado compartido cruza por `platform/`, NO por
imports cross-agent (`dashboard → sales_whatsapp` rompe R-DIP).

`FilesystemMessageHistoryStore` ya vive en `platform/session_history/` por el
mismo razonamiento (lo necesitan sales, remarketing y el dashboard handoff).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class FilesystemMetadataStore:
    """Adapter filesystem del documento de metadatos por sesion.

    Cada sesion mapea a ``<vault_dir>/<session_id>/metadata.json``. La lectura
    es tolerante a archivos corruptos (retorna ``{}`` ante ``JSONDecodeError``);
    la escritura es atomica por simple ``write_text`` (mismo comportamiento que
    el legado en ``service.py``).

    Previo: vivia en `src/sales_whatsapp/state.py` cuando solo sales lo usaba.
    Movido a `platform/` cuando `dashboard/handoff.py` empezo a leer/escribir
    el mismo `metadata.json` (regla DEHA: estado compartido en `platform/`).
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
