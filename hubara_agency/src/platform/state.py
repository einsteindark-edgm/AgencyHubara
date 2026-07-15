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

import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Any) -> None:
    """Escribe `data` como JSON a `path` de forma atomica (temp + os.replace).

    Por que atomico: `metadata.json` lo escriben varios procesos sin lock (el
    ingest del webhook, tools como `register_order` / `set_order_slot`, la red
    de seguridad de cierre, y `dashboard/handoff.py`). Un `write_text` plano
    hace truncate+write: un lector concurrente puede leer el archivo a medio
    escribir, caer en `JSONDecodeError` y -- via el `read()` tolerante de
    abajo -- recibir `{}`, pisando el estado real en el siguiente write.
    Escribir a un temp en el MISMO directorio y `os.replace` garantiza que un
    lector siempre vea el archivo viejo COMPLETO o el nuevo COMPLETO, nunca uno
    roto (rename es atomico dentro del mismo filesystem).

    `ensure_ascii=False`: mantiene acentos/enies legibles en disco, consistente
    con lo que ya escribe `register_order`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        # Limpieza best-effort del temp si algo falla antes del replace.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class FilesystemMetadataStore:
    """Adapter filesystem del documento de metadatos por sesion.

    Cada sesion mapea a ``<vault_dir>/<session_id>/metadata.json``. La lectura
    es tolerante a archivos corruptos (retorna ``{}`` ante ``JSONDecodeError``);
    la escritura es **atomica** (temp file + ``os.replace`` via
    ``atomic_write_json``) para no exponer archivos a medio escribir a lectores
    concurrentes (ingest, tools, dashboard handoff comparten el archivo).

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
        atomic_write_json(self._path_for(session_id), data)

    def update(
        self,
        session_id: str,
        mutator: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        """Read-modify-write ATOMICO bajo un lock por sesion (fcntl.flock).

        `atomic_write_json` evita torn reads, pero un ciclo read→mutate→write
        sin lock sigue perdiendo updates entre writers concurrentes (dos
        requests del dashboard, o dashboard + ingest del webhook): el segundo
        write pisa lo que el primero agrego. Este metodo toma un flock sobre un
        sidecar `metadata.json.lock`, lee FRESCO adentro del lock, aplica
        `mutator` y escribe — dos `update()` concurrentes se serializan.

        `mutator` recibe el dict fresco y devuelve el dict a escribir, o
        ``None`` para ABORTAR sin escribir (p.ej. si la lectura fresca vino
        vacia por un error transitorio y escribir el dict mutado pisaria el
        estado real de la sesion — el modo de fallo "el bot revive en medio de
        la intervencion humana").

        Solo protege contra otros callers de `update()`; los `write()` planos
        legacy siguen corriendo carrera (ventana reducida por lectura fresca).
        Devuelve el dict escrito, o ``None`` si el mutator aborto.
        """
        path = self._path_for(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.parent / f"{path.name}.lock"
        with open(lock_path, "w", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            try:
                result = mutator(self.read(session_id))
                if result is None:
                    return None
                atomic_write_json(path, result)
                return result
            finally:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
