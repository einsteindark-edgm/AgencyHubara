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

Una sola forma de escribir `metadata.json` (incidente 2026-10-06)
-----------------------------------------------------------------
El turno 4 de una conversación de prueba mandó la foto de un producto; el
flush la sacó de `pending_ui_intents` y anotó su entrega. Un escritor que había
leído ANTES escribió su copia entera DESPUÉS: la foto volvió a la cola, salió
otra vez en el turno 5 y su entrega desapareció de `outbound_media_index`.
Desde entonces TODA escritura del documento pasa por `FilesystemMetadataStore`,
con el candado por sesión y atómica, y cada escritor toca SOLO lo suyo:

  * ``update(session_id, mutator)`` — el escritor aplica sus llaves sobre la
    lectura fresca (la forma por defecto);
  * ``write_merged(session_id, base=..., ours=...)`` — para quien acumula
    cambios entre esperas (el ingest esperando a Jev): lleva solo lo que cambió
    frente a lo que leyó (``merge_changes``, merge de tres vías).

El gate `tests/platform/test_metadata_json_single_writer.py` (marca
`architecture`, paso propio en el CI de arquitectura) impide que vuelva otro
camino.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from src.platform.constants import WHATSAPP_SESSION_PREFIX


def atomic_write_json(path: Path, data: Any) -> None:
    """Escribe `data` como JSON a `path` de forma atomica (temp + os.replace).

    Por que atomico: un `write_text` plano hace truncate+write: un lector
    concurrente puede leer el archivo a medio escribir, caer en
    `JSONDecodeError` y -- via el `read()` tolerante de abajo -- recibir `{}`,
    pisando el estado real en el siguiente write. Escribir a un temp en el
    MISMO directorio y `os.replace` garantiza que un lector siempre vea el
    archivo viejo COMPLETO o el nuevo COMPLETO, nunca uno roto (rename es
    atomico dentro del mismo filesystem).

    Para `metadata.json` no se llama directo: va por `FilesystemMetadataStore`
    (candado por sesión + solo las llaves del escritor).

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


# Un `session_id` es el NOMBRE de un directorio del vault
# (`<vault_dir>/<session_id>/...`). El charset no puede ni expresar un
# traversal -- sin `.`, sin `/`, sin `\` -- y el tope de largo evita que un
# segmento desmedido reviente `Path.exists()` (ENAMETOOLONG). Cubre las formas
# que existen de verdad: `wa_<digitos>` (el `from` de Meta), `wa_+<digitos>`
# (prefijo E.164) e ids de test con guion bajo.
_VAULT_SESSION_ID_RE = re.compile(
    re.escape(WHATSAPP_SESSION_PREFIX) + r"[A-Za-z0-9+_]{1,120}"
)


def is_vault_session_id(session_id: str) -> bool:
    """True si `session_id` puede nombrar el directorio de una sesion del vault.

    Es el PISO anti path-traversal para todo id que llega de afuera (URL, body)
    y termina en un `Path` bajo el vault: `..` es el padre del vault, `.` el
    vault mismo y `_analytics` / `_campaigns` directorios que no son sesiones.
    NO es politica de formato: quien escribe sobre un numero real (enviar,
    registrar un pedido) puede exigir ademas `wa_<digitos>`.

    `fullmatch` y no `match`: el `$` de `match` acepta un salto de linea final.
    """
    return _VAULT_SESSION_ID_RE.fullmatch(session_id) is not None


# =============================================================================
# merge de tres vías
# =============================================================================


class _Missing:
    """La llave (o el elemento) no está de ese lado."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - solo para depurar
        return "<ausente>"


_MISSING: Any = _Missing()

#: Llaves de identidad de los elementos de una lista de dicts, de la más
#: específica a la más general: `episode_id` también aparece como REFERENCIA
#: dentro de otras entradas (fotos, eventos de CAPI), así que va al final.
_IDENTITY_KEYS: tuple[str, ...] = ("id", "wa_message_id", "event_id", "media_id", "episode_id")


def merge_changes(base: Any, ours: Any, fresh: Any) -> Any:
    """Merge de tres vías: lo que el escritor cambió, sobre lo que hay en disco.

    * ``base``: lo que el escritor leyó (copia tomada al leer);
    * ``ours``: lo que el escritor quiere escribir (``base`` + sus cambios);
    * ``fresh``: lo que hay en disco AHORA (otro escritor pudo cambiarlo).

    Reglas, recursivas:

    * subárbol que el escritor no tocó (``ours == base``) → gana ``fresh``;
    * subárbol que solo tocó el escritor (``fresh == base``) → gana ``ours``;
    * dicts → llave por llave (borrar una llave también es un cambio);
    * listas de dicts con identidad (``id``, ``wa_message_id``, ``event_id``,
      ``media_id``, ``episode_id``, única en cada lado) → elemento por
      elemento: el orden es el de disco y lo que agregó el escritor va al final;
    * listas sin identidad a las que los dos solo agregaron al final → las dos
      colas (sin repetir lo que ya está en disco);
    * conflicto en una hoja (los dos cambiaron lo mismo distinto) → gana el
      escritor.

    No muta sus entradas; el resultado puede compartir subárboles con ellas.
    """
    return _merge(base, ours, fresh)


def _merge(base: Any, ours: Any, fresh: Any) -> Any:
    if ours == base:
        return fresh
    if fresh == base:
        return ours
    if ours == fresh:
        return ours
    if isinstance(ours, dict) and isinstance(fresh, dict):
        if base is _MISSING:
            base = {}
        if isinstance(base, dict):
            return _merge_dicts(base, ours, fresh)
    if isinstance(ours, list) and isinstance(fresh, list):
        if base is _MISSING:
            base = []
        if isinstance(base, list):
            merged = _merge_lists(base, ours, fresh)
            if merged is not None:
                return merged
    return ours  # conflicto en una hoja: gana el escritor


def _merge_dicts(base: dict[str, Any], ours: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in fresh.items():
        merged = _merge(base.get(key, _MISSING), ours.get(key, _MISSING), value)
        if merged is not _MISSING:
            out[key] = merged
    for key, value in ours.items():
        if key in fresh:
            continue
        merged = _merge(base.get(key, _MISSING), value, _MISSING)
        if merged is not _MISSING:
            out[key] = merged
    return out


def _merge_lists(base: list[Any], ours: list[Any], fresh: list[Any]) -> list[Any] | None:
    key = _identity_key(base, ours, fresh)
    if key is not None:
        return _merge_keyed(key, base, ours, fresh)
    size = len(base)
    if ours[:size] == base and fresh[:size] == base:
        # Los dos solo agregaron al final: van las dos colas.
        fresh_tail = fresh[size:]
        return [*fresh, *(item for item in ours[size:] if item not in fresh_tail)]
    return None


def _identity_key(*lists: list[Any]) -> str | None:
    """La llave que identifica cada elemento en las tres listas, o None si son
    escalares, faltan llaves o alguna se repite."""
    if not all(isinstance(item, dict) for items in lists for item in items):
        return None
    for key in _IDENTITY_KEYS:
        if all(_unique_identities(items, key) for items in lists):
            return key
    return None


def _unique_identities(items: list[dict[str, Any]], key: str) -> bool:
    seen: set[Any] = set()
    for item in items:
        value = item.get(key)
        if isinstance(value, bool) or not isinstance(value, (str, int)) or value in seen:
            return False
        seen.add(value)
    return True


def _merge_keyed(key: str, base: list[dict[str, Any]], ours: list[dict[str, Any]], fresh: list[dict[str, Any]]) -> list[Any]:
    base_by = {item[key]: item for item in base}
    ours_by = {item[key]: item for item in ours}
    fresh_ids = {item[key] for item in fresh}
    out: list[Any] = []
    for item in fresh:
        merged = _merge(base_by.get(item[key], _MISSING), ours_by.get(item[key], _MISSING), item)
        if merged is not _MISSING:
            out.append(merged)
    for item in ours:
        if item[key] in fresh_ids:
            continue
        merged = _merge(base_by.get(item[key], _MISSING), item, _MISSING)
        if merged is not _MISSING:
            out.append(merged)
    return out


# =============================================================================
# el store
# =============================================================================

# Candados que el hilo actual ya tiene (por archivo). `flock` no es reentrante
# entre descriptores del mismo proceso: un mutator que escribiera la misma
# sesión colgaría el worker para siempre. Dentro del mismo hilo se reusa el
# candado (no hay `await` dentro de un mutator, así que otra corrutina no puede
# colarse); otros hilos y otros procesos esperan como siempre.
_held = threading.local()


class FilesystemMetadataStore:
    """Adapter filesystem del documento de metadatos por sesion.

    Cada sesion mapea a ``<vault_dir>/<session_id>/metadata.json``. La lectura
    es tolerante a archivos corruptos (retorna ``{}`` ante ``JSONDecodeError``);
    TODA escritura es **atomica** (temp file + ``os.replace`` via
    ``atomic_write_json``) y va bajo el MISMO candado por sesion (``flock``
    sobre ``metadata.json.lock``): ninguna escritura cae entre la lectura y la
    escritura de otra.

    Formas de escribir (ver el docstring del módulo):
      * ``update(session_id, mutator)`` — solo las llaves del escritor sobre la
        lectura fresca (la de siempre);
      * ``write_merged(session_id, base=..., ours=...)`` — para quien acumula
        cambios entre esperas;
      * ``write(session_id, data)`` — reemplazo entero; fuera de este módulo
        no se usa (lo prohíbe `tests/platform/test_metadata_json_single_writer.py`).

    Previo: vivia en `src/sales_whatsapp/state.py` cuando solo sales lo usaba.
    Movido a `platform/` cuando `dashboard/handoff.py` empezo a leer/escribir
    el mismo `metadata.json` (regla DEHA: estado compartido en `platform/`).
    """

    def __init__(self, vault_dir: Path) -> None:
        self._vault_dir = vault_dir

    def _path_for(self, session_id: str) -> Path:
        return self._vault_dir / session_id / "metadata.json"

    @contextmanager
    def _locked(self, path: Path) -> Iterator[None]:
        """El candado de la sesión (`flock` sobre el sidecar `.lock`),
        reentrante dentro del mismo hilo."""
        held: set[str] = getattr(_held, "paths", None) or set()
        _held.paths = held
        key = str(path)
        if key in held:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.parent / f"{path.name}.lock"
        with open(lock_path, "w", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            held.add(key)
            try:
                yield
            finally:
                held.discard(key)
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

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
        """Reemplaza el documento ENTERO (bajo el candado). Pisa lo que otro
        escritor haya puesto desde que se leyó: fuera de este módulo, usar
        ``update`` o ``write_merged``."""
        path = self._path_for(session_id)
        with self._locked(path):
            atomic_write_json(path, data)

    def update(
        self,
        session_id: str,
        mutator: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        """Read-modify-write ATOMICO bajo el candado por sesion (fcntl.flock).

        `atomic_write_json` evita torn reads, pero un ciclo read→mutate→write
        sin lock pierde updates entre writers concurrentes: el segundo write
        pisa lo que el primero agrego. Este metodo toma el candado, lee FRESCO
        adentro, aplica `mutator` y escribe — dos escrituras concurrentes se
        serializan (``write`` y ``write_merged`` toman el mismo candado).

        `mutator` recibe el dict fresco y devuelve el dict a escribir, o
        ``None`` para ABORTAR sin escribir (p.ej. si la lectura fresca vino
        vacia por un error transitorio y escribir el dict mutado pisaria el
        estado real de la sesion — el modo de fallo "el bot revive en medio de
        la intervencion humana"). El mutator no hace I/O lento ni ``await``:
        corre con el candado tomado.

        Devuelve el dict escrito, o ``None`` si el mutator aborto.
        """
        path = self._path_for(session_id)
        with self._locked(path):
            result = mutator(self.read(session_id))
            if result is None:
                return None
            atomic_write_json(path, result)
            return result

    def write_merged(
        self, session_id: str, *, base: dict[str, Any], ours: dict[str, Any]
    ) -> dict[str, Any]:
        """Escribe SOLO lo que el escritor cambió frente a lo que leyó.

        Para escritores que acumulan cambios entre esperas (el ingest esperando
        a Jev, una tool que consulta el catálogo): ``base`` es la copia
        profunda de lo que leyeron; ``ours``, su dict con los cambios. Bajo el
        candado se relee FRESCO y se escribe ``merge_changes(base, ours,
        fresh)``: lo que otro escritor puso mientras tanto no se revierte.

        Lectura fresca vacía para una sesión que tenía datos (ilegible): no se
        mezcla sobre la nada — quedarían solo las llaves del escritor —; se
        escribe ``ours`` entero, como antes. Devuelve lo escrito.
        """
        path = self._path_for(session_id)
        with self._locked(path):
            fresh = self.read(session_id)
            if not fresh and base:
                merged = ours
            else:
                merged = merge_changes(base, ours, fresh)
                if merged is fresh:
                    return fresh  # el escritor no cambió nada
            atomic_write_json(path, merged)
            return merged
