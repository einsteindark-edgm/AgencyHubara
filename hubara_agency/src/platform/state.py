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

Un `metadata.json` dañado se recupera solo, dentro del store (ver
`FilesystemMetadataStore`): la última copia buena queda en
`metadata.json.prev`; el que lee la usa con una alerta y nunca escribe, y el
que escribe aparta el dañado y repara sobre esa copia. Un error pasajero
(EMFILE, EIO…) falla y se reintenta. Nada pasa al equipo humano por un
archivo dañado.
"""
from __future__ import annotations

import errno
import fcntl
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

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
    * listas sin identidad: si el escritor solo agregó al final (``ours``
      empieza con ``base``) → lo de disco + lo que agregó; si el otro solo
      agregó (``fresh`` empieza con ``base``) → la lista del escritor + lo que
      agregó el otro; si no, conflicto. Sin deduplicar: sin identidad, dos
      entradas iguales no son necesariamente la misma;
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
    # Regla simple y simétrica (segunda revisión del PR #393): lo que uno solo
    # agregó va encima de lo que dejó el otro, sea lo que sea (el otro pudo
    # sacar, recortar o meter en medio). Tras una escritura del ingest, su
    # `base` es SU vista: lo que otro escritor agregó antes queda en disco y
    # se conserva igual.
    size = len(base)
    if ours[:size] == base:
        return [*fresh, *ours[size:]]
    if fresh[:size] == base:
        return [*ours, *fresh[size:]]
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

log = logging.getLogger(__name__)

_T = TypeVar("_T")

# Candados que el hilo actual ya tiene (por archivo REAL: `realpath`, así el
# mismo archivo por un symlink es el mismo candado). `flock` no es reentrante
# entre descriptores del mismo proceso: un mutator que escribiera la misma
# sesión colgaría el worker para siempre. Dentro del mismo hilo se reusa el
# candado (no hay `await` dentro de un mutator, así que otra corrutina no puede
# colarse) y se avisa: una escritura anidada pisa lo que el mutator de afuera
# escriba después. Otros hilos y otros procesos esperan como siempre.
_held = threading.local()


#: La última copia buena (se rota DESPUÉS de cada escritura sana).
PREV_SUFFIX = ".prev"
#: Un documento dañado, apartado una vez antes de reescribirlo.
DAMAGED_SUFFIX = ".damaged"

#: Errores de lectura que son DAÑO del archivo (no se arreglan reintentando):
#: sin permiso, o en su lugar hay un directorio o un enlace roto. Cualquier
#: otro error del sistema (EMFILE, ENFILE, ENOMEM, EAGAIN, ESTALE, EIO…) es
#: PASAJERO: no dice nada del documento, y tratarlo como daño pisaría uno sano.
_DAMAGE_ERRNOS = frozenset({errno.EACCES, errno.EPERM, errno.EISDIR, errno.ENOTDIR, errno.ELOOP})

#: Los errores PASAJEROS que se reintentan.
TRANSIENT_ERRNOS = frozenset({errno.EMFILE, errno.ENFILE, errno.ENOMEM, errno.EAGAIN, errno.ESTALE, errno.EIO})
#: Quien ESCRIBE reintenta un error pasajero (al releer o al escribir), bajo el
#: candado de la sesión y con esperas cortas: un EIO de un instante no hace
#: fallar la anotación de algo que ya pasó (octava revisión; a11a150b releía
#: una vez). `read()` no reintenta: corre en el bucle async.
_WRITE_RETRY_DELAYS_S: tuple[float, ...] = (0.02, 0.06, 0.15)


class _Absent:
    """El archivo no existe."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - solo para depurar
        return "<no existe>"


_ABSENT: Any = _Absent()


def _load(path: Path) -> Any:
    """`path` tal cual está en disco: el dict, `_ABSENT` si no existe, o
    ``None`` si está DAÑADO (sin permiso, es un directorio, bytes que no son
    UTF-8, JSON roto o vacío, o algo que no es un objeto). Un error PASAJERO
    del sistema se lanza tal cual: no es daño del archivo."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _ABSENT
    except UnicodeDecodeError:
        return None
    except OSError as exc:
        if exc.errno in _DAMAGE_ERRNOS:
            return None
        raise
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


@dataclass(frozen=True)
class _Snapshot:
    """El documento de una sesión listo para usar, y de dónde salió."""

    data: dict[str, Any]
    #: ``ok`` (se leyó bien) · ``new`` (no existe ni hay copia: sesión nueva) ·
    #: ``damaged`` (existe y está dañado: ``data`` es la última copia buena, o
    #: ``{}`` si no hay) · ``missing`` (no existe pero hay copia: ``data`` es
    #: esa copia).
    state: str


def _prev_of(path: Path) -> Path:
    return path.with_name(path.name + PREV_SUFFIX)


def _snapshot(path: Path) -> _Snapshot:
    """El documento de la sesión y de dónde salió. Un intento por archivo, sin
    esperas. Un error PASAJERO leyendo el documento o su copia buena se lanza:
    nadie decide (ni escribe) sobre algo que no pudo leer."""
    current = _load(path)
    if isinstance(current, dict):
        return _Snapshot(current, "ok")
    prev = _load(_prev_of(path))
    if current is _ABSENT:
        if prev is _ABSENT:
            return _Snapshot({}, "new")
        # Sin candado, un escritor pudo crear el documento (y su copia) entre
        # las dos lecturas (sesión nueva con dos escrituras seguidas): se relee
        # una vez antes de darlo por perdido.
        current = _load(path)
        if isinstance(current, dict):
            return _Snapshot(current, "ok")
    recovered = prev if isinstance(prev, dict) else {}
    return _Snapshot(recovered, "missing" if current is _ABSENT else "damaged")


# Una alerta por episodio de daño (no una por lectura: el daño sigue hasta que
# alguien escribe, y la bandeja lee todas las sesiones a cada rato). Estado del
# proceso: `reset_damage_tracking()` lo limpia (las pruebas lo llaman antes y
# después de cada una, lección L-2).
_TRACKING_LOCK = threading.Lock()
_ALERTED: dict[str, tuple[Any, ...]] = {}


def _file_signature(path: Path) -> tuple[int, int, int] | None:
    """Qué archivo hay en `path` (inodo, tamaño, fecha), o ``None`` si no hay."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def _episode_signature(path: Path, snapshot: _Snapshot) -> tuple[Any, ...]:
    """Qué daño es: el mismo archivo dañado (inodo, tamaño, fecha) es el mismo
    episodio; otro daño después de reparar es otro."""
    target = _prev_of(path) if snapshot.state == "missing" else path
    return (snapshot.state, _file_signature(target))


def _alert_once(path: Path, snapshot: _Snapshot) -> None:
    """Un documento dañado (o que desapareció dejando su copia) es un problema
    técnico: se avisa con un ERROR, una vez por episodio, y se sigue con la
    copia recuperada."""
    if snapshot.state not in ("damaged", "missing"):
        return
    key = os.path.realpath(path)
    signature = _episode_signature(path, snapshot)
    with _TRACKING_LOCK:
        if _ALERTED.get(key) == signature:
            return
        _ALERTED[key] = signature
    what = "no existe pero quedó su copia" if snapshot.state == "missing" else "no se puede leer"
    used = "uso la última copia buena" if snapshot.data else "no hay copia buena: sigo desde vacío"
    log.error(
        "metadata dañado en %s (%s): %s",
        path.parent.name,
        what,
        used,
        extra={"path": str(path), "metadata_state": snapshot.state},
    )


def _episode_over(path: Path) -> None:
    with _TRACKING_LOCK:
        _ALERTED.pop(os.path.realpath(path), None)


def reset_damage_tracking() -> None:
    """Olvida los episodios de daño ya alertados (estado del proceso). Para
    las pruebas: una no hereda el de otra."""
    with _TRACKING_LOCK:
        _ALERTED.clear()


def _prev_temp_of(path: Path) -> Path:
    """Dónde espera la versión vieja mientras se escribe: un nombre FIJO por
    sesión (bajo el candado hay un solo escritor). Un SIGKILL entre escribir y
    promoverla lo deja; la próxima escritura lo reemplaza: no se acumulan."""
    return path.with_name(f".{path.name}{PREV_SUFFIX}.tmp")


def _link_previous(path: Path) -> Path | None:
    """ANTES de escribir: la versión sana actual se enlaza al temporal (si el
    filesystem no tiene enlaces, se copia). Pasa a ser `.prev` solo si la
    escritura termina bien (`_promote_previous`)."""
    tmp = _prev_temp_of(path)
    _discard(tmp)
    try:
        try:
            os.link(path, tmp)
        except OSError:
            shutil.copy2(path, tmp)
        return tmp
    except OSError as exc:
        log.warning("metadata_prev_not_kept", extra={"path": str(path), "error": repr(exc)[:200]})
        _discard(tmp)
        return None


def _promote_previous(tmp: Path, path: Path) -> None:
    """La escritura terminó bien: el temporal (la versión anterior) pasa a ser
    la copia buena, de una vez (`os.replace`)."""
    try:
        os.replace(tmp, _prev_of(path))
    except OSError as exc:
        log.warning("metadata_prev_not_kept", extra={"path": str(path), "error": repr(exc)[:200]})
        _discard(tmp)


def _discard(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


@dataclass(frozen=True)
class _SetAside:
    path: Path
    #: True: se movió (el lugar quedó vacío hasta escribir); False: enlace
    #: (el dañado sigue en su lugar hasta el reemplazo atómico).
    moved: bool


def _set_aside_damaged(path: Path) -> _SetAside | None:
    """Aparta el documento dañado UNA vez, tal cual, como
    `metadata.json.damaged-<ms>` (para repararlo a mano si hace falta). Si no
    se puede, se sigue igual: nunca traba la escritura."""
    stamp = time.time_ns() // 1_000_000
    target = path.with_name(f"{path.name}{DAMAGED_SUFFIX}-{stamp}")
    suffix = 1
    while os.path.lexists(target):
        target = path.with_name(f"{path.name}{DAMAGED_SUFFIX}-{stamp}-{suffix}")
        suffix += 1
    try:
        os.link(path, target)
        return _SetAside(target, moved=False)
    except FileNotFoundError:
        return None
    except OSError:
        pass
    try:
        os.replace(path, target)
        return _SetAside(target, moved=True)
    except OSError as exc:
        log.error("metadata_damaged_not_set_aside", extra={"path": str(path), "error": repr(exc)[:200]})
        return None


def _put_back(path: Path, aside: _SetAside) -> None:
    """La escritura falló: lo apartado vuelve a como estaba (`metadata.json`
    nunca queda ausente; la próxima escritura lo aparta otra vez)."""
    try:
        if aside.moved:
            os.replace(aside.path, path)
        else:
            os.unlink(aside.path)
    except OSError as exc:
        log.error("metadata_damaged_not_put_back", extra={"path": str(path), "error": repr(exc)[:200]})


def _retrying_transient(operation: Callable[[], _T], path: Path, what: str) -> _T:
    """`operation()`; ante un error PASAJERO espera un poco y la repite, hasta 3
    veces (`_WRITE_RETRY_DELAYS_S`). Otro error, o el pasajero tras el último
    reintento, se lanza. Solo para quien escribe, bajo el candado."""
    for attempt, delay in enumerate(_WRITE_RETRY_DELAYS_S, start=1):
        try:
            return operation()
        except OSError as exc:
            if exc.errno not in TRANSIENT_ERRNOS:
                raise
            log.warning(
                "metadata_write_retry",
                extra={"path": str(path), "what": what, "attempt": attempt, "error": repr(exc)[:200]},
            )
        time.sleep(delay)
    return operation()


def _replace_document(path: Path, data: dict[str, Any], snapshot: _Snapshot) -> None:
    """Escribe `data` (atómico) cuidando las copias. Desde un documento sano, la
    versión anterior pasa a `.prev` SOLO si la escritura termina bien (si
    falla, la copia buena queda como estaba y en otro inodo). Uno dañado se
    aparta (y vuelve a su lugar si la escritura falla); a la copia buena no la
    toca un dañado."""
    previous: Path | None = None
    aside: _SetAside | None = None
    if snapshot.state == "ok":
        previous = _link_previous(path)
    elif snapshot.state == "damaged":
        aside = _set_aside_damaged(path)
    try:
        _retrying_transient(lambda: atomic_write_json(path, data), path, "escritura")
    except BaseException:
        if previous is not None:
            _discard(previous)
        if aside is not None:
            _put_back(path, aside)
        raise
    if previous is not None:
        _promote_previous(previous, path)
    _episode_over(path)


class FilesystemMetadataStore:
    """Adapter filesystem del documento de metadatos por sesion.

    Cada sesion mapea a ``<vault_dir>/<session_id>/metadata.json``. TODA
    escritura es **atomica** (temp file + ``os.replace`` via
    ``atomic_write_json``) y va bajo el MISMO candado por sesion (``flock``
    sobre ``metadata.json.lock``).

    Un ``metadata.json`` dañado (decisión del operador, 2026-10-06) es un
    problema técnico: NO pasa la conversación al equipo humano ni traba
    escrituras. La regla, simple:

      * **Daño** es lo que no se arregla reintentando: el contenido (JSON roto,
        vacío, bytes que no son UTF-8, algo que no es un objeto) o
        permisos/forma (EACCES/EPERM, un directorio o un enlace roto en su
        lugar). Un **error pasajero** del sistema (EMFILE, ENFILE, ENOMEM,
        EAGAIN, ESTALE, EIO…) no dice nada del documento.
      * **El que lee nunca escribe.** ``read()`` hace un intento, sin esperas
        ni hilos. Dañado → la última copia buena (``metadata.json.prev``, o
        ``{}`` si no hay) con UN log de ERROR por episodio de daño («metadata
        dañado en <sesión>»), y el disco queda como estaba. No existe y no hay
        copia → ``{}`` (sesión nueva). No existe pero hay copia → la copia, con
        la alerta (antes se relee el documento una vez: un escritor pudo
        crearlo en medio). Error pasajero → LANZA: nunca una copia vieja ni
        ``{}`` por un error pasajero, así nadie que lee → decide → escribe
        decide sobre algo que no pudo leer (Temporal reintenta la activity).
      * **El que escribe repara**, bajo el candado (``update``,
        ``write_merged``, ``write``): relee fresco. Si está dañado, lo aparta
        UNA vez como ``metadata.json.damaged-<ms>``, aplica su cambio sobre la
        copia buena (o ``{}``) y escribe atómico; si la escritura falla, lo
        apartado vuelve a su lugar (``metadata.json`` nunca queda ausente). Un
        error pasajero al releer o al escribir se reintenta hasta 3 veces con
        esperas cortas (20, 60 y 150 ms; bajo el candado de esa sesión) y
        recién después lanza, sin escribir.
      * **Copia buena**: cada escritura sobre un documento sano deja la versión
        que reemplaza como ``.prev``. Se enlaza (sin enlaces, se copia) a
        ``.metadata.json.prev.tmp`` —nombre fijo: bajo el candado hay un solo
        escritor, así que un SIGKILL deja a lo sumo uno y la próxima escritura
        lo reemplaza— y pasa a ``.prev`` solo si la escritura termina bien.
        Desde un documento dañado no se rota. Una sola copia por sesión.
      * **A lo sumo una escritura atrás (N-1)**: un documento dañado se
        recupera como estaba antes de su ÚLTIMA escritura, y lo que esa
        escritura cambió se pierde (queda solo en el apartado). Si lo último
        fue la toma del operador, vuelve la ruta anterior y el bot puede
        contestar hasta que el operador la tome otra vez. El caso espejo: si lo
        último fue «devolver al bot», un daño justo después la deja otra vez en
        humano — es la versión anterior, no un paso nuevo a humano (sin motivo
        ni escalación nuevos). Si fue el flush que sacó una foto, la foto
        vuelve a la cola pero no sale otra vez (la frena el registro de
        entregas, ``ui_intents_delivered.jsonl``, fuera de ``metadata.json``) y
        su entrada de ``outbound_media_index`` se pierde. Se acepta porque con
        escrituras atómicas el daño en el lugar casi no ocurre.
      * ``.prev`` y ``.damaged-*`` los escribe solo este módulo; nadie más
        escribe ``metadata.json`` (gate de un solo escritor, sobre ``src/`` y
        ``scripts/``).

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
        reentrante dentro del mismo hilo (por archivo real)."""
        held: set[str] = getattr(_held, "paths", None) or set()
        _held.paths = held
        key = os.path.realpath(path)
        if key in held:
            log.warning("metadata_nested_write_reentrant", extra={"path": key})
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

    def _fresh(self, path: Path) -> _Snapshot:
        """Lo que ve quien LEE (ver la clase), con su alerta: un intento."""
        snapshot = _snapshot(path)
        _alert_once(path, snapshot)
        return snapshot

    def _fresh_locked(self, path: Path) -> _Snapshot:
        """Lo que ve quien ESCRIBE, bajo el candado: un error pasajero se
        reintenta (esperas cortas) antes de lanzar."""
        snapshot = _retrying_transient(lambda: _snapshot(path), path, "relectura")
        _alert_once(path, snapshot)
        return snapshot

    def read(self, session_id: str) -> dict[str, Any]:
        """El documento de la sesión (``{}`` si es nueva). Sin candado, sin
        esperas y sin escribir nada: dañado → la última copia buena (o ``{}``)
        con la alerta; error pasajero → lanza (ver la clase)."""
        return self._fresh(self._path_for(session_id)).data

    def write(self, session_id: str, data: dict[str, Any]) -> None:
        """Reemplaza el documento ENTERO (bajo el candado). Pisa lo que otro
        escritor haya puesto desde que se leyó: fuera de este módulo, usar
        ``update`` o ``write_merged``."""
        path = self._path_for(session_id)
        with self._locked(path):
            _replace_document(path, data, self._fresh_locked(path))

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

        `mutator` recibe el dict fresco (``{}`` si la sesión no existe todavía;
        la última copia buena si el documento está dañado) y devuelve el dict a
        escribir, o ``None`` para ABORTAR sin escribir. El mutator no hace I/O
        lento ni ``await``: corre con el candado tomado. Un error pasajero al
        leer lanza sin llamar al mutator.

        Devuelve el dict escrito, o ``None`` si el mutator abortó.
        """
        path = self._path_for(session_id)
        with self._locked(path):
            snapshot = self._fresh_locked(path)
            result = mutator(snapshot.data)
            if result is None:
                return None
            _replace_document(path, result, snapshot)
            return result

    def write_merged(
        self,
        session_id: str,
        *,
        base: dict[str, Any],
        ours: dict[str, Any],
        before_merge: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Escribe SOLO lo que el escritor cambió frente a lo que leyó.

        Para escritores que acumulan cambios entre esperas (el ingest esperando
        a Jev, una tool que consulta el catálogo): ``base`` es la copia
        profunda de lo que leyeron; ``ours``, su dict con los cambios. Bajo el
        candado se relee FRESCO y se escribe ``merge_changes(base, ours,
        fresh)``: lo que otro escritor puso mientras tanto no se revierte.

        ``before_merge(fresh)``: se llama bajo el candado con lo que hay en
        disco AHORA, antes del merge; puede ajustar ``ours`` (el ingest cede lo
        suyo si un humano tomó la conversación mientras esperaba — decidido
        acá y no con una lectura previa, que deja una ventana).

        Documento dañado: ``fresh`` es la última copia buena (ver la clase).
        Sin nada fresco (sesión que desapareció, o dañada sin copia buena) y
        con ``base`` con datos, se escribe ``ours`` entero: lo que el escritor
        leyó es lo más completo que hay. Un error pasajero al leer lanza sin
        escribir. Devuelve lo escrito.
        """
        path = self._path_for(session_id)
        with self._locked(path):
            snapshot = self._fresh_locked(path)
            fresh = snapshot.data
            if not fresh and base:
                merged = ours
            else:
                if before_merge is not None:
                    before_merge(fresh)
                merged = merge_changes(base, ours, fresh)
                if merged is fresh and snapshot.state in ("ok", "new"):
                    return fresh  # el escritor no cambió nada
            _replace_document(path, merged, snapshot)
            return merged
