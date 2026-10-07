"""Recuperación automática de un `metadata.json` dañado (decisión del operador,
2026-10-06, PR #393).

Un archivo dañado es un problema técnico: NO pasa la conversación al equipo
humano ni traba escrituras. El store se recupera solo, sin que ningún llamador
cambie (sexta revisión: la regla simple):

  * cada escritura sobre un documento que se leyó bien deja la versión
    anterior como `metadata.json.prev` (la última copia buena), rotada solo
    si la escritura termina bien;
  * el que lee NUNCA escribe: un documento dañado se lee como `.prev` con un
    log de ERROR (una vez por episodio de daño) y el disco queda igual;
  * el que escribe repara: aparta el dañado UNA vez como
    `metadata.json.damaged-<ms>`, aplica su cambio sobre la copia recuperada
    (`.prev` o `{}`) y escribe;
  * un error de lectura PASAJERO (EMFILE, EIO…) no es daño: leer y escribir
    lanzan y nada cambia (Temporal reintenta).

Sin esperas ni hilos. La copia buena va a lo sumo una escritura atrás (N-1).
"""
from __future__ import annotations

import errno
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

import pytest

import src.platform.state as state
from src.platform.state import FilesystemMetadataStore

SID = "wa_573001234567"

_GOOD = {
    "phone_number_id": "pnid-1",
    "active_route": "humano",
    "tag": "HUMANO",
    "motivo": "Comprobante de pago: verificar",
    "registered_order": {"order_id": "order_1"},
}


def _session(vault: Path) -> Path:
    path = vault / SID / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _prev(path: Path) -> Path:
    return path.with_name("metadata.json.prev")


def _damaged_copies(path: Path) -> list[Path]:
    return sorted(path.parent.glob("metadata.json.damaged-*"))


def _broken_json(path: Path) -> None:
    path.write_text('{"active_route": "humano", "episodes": [', encoding="utf-8")


def _empty_file(path: Path) -> None:
    path.write_bytes(b"")


def _not_utf8(path: Path) -> None:
    path.write_bytes(b'{"active_route": "humano", "x": "\xff\xfe"}')


def _no_permission(path: Path) -> None:
    path.write_text(json.dumps({**_GOOD, "tag": "OTRO"}), encoding="utf-8")
    os.chmod(path, 0)


_DAMAGE = [
    pytest.param(_broken_json, id="json-roto"),
    pytest.param(_empty_file, id="cero-bytes"),
    pytest.param(_not_utf8, id="utf8-invalido"),
    pytest.param(
        _no_permission,
        id="permisos-000",
        marks=pytest.mark.skipif(os.geteuid() == 0, reason="root lee archivos 000"),
    ),
]


def _bytes(path: Path) -> bytes:
    os.chmod(path, 0o644)
    return path.read_bytes()


def _alerts(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.ERROR and "metadata dañado" in r.getMessage()]


# --- leer -----------------------------------------------------------------------


def _disk(path: Path) -> dict[str, tuple[int, int, int, int]]:
    """Qué hay en la carpeta de la sesión (sin el candado): nombre → inodo,
    tamaño, fecha y permisos. Cualquier escritura del store lo cambia (escribe
    un inodo nuevo, aparta, rota la copia) sin tener que leer un archivo 000."""
    out = {}
    for entry in path.parent.iterdir():
        if entry.name.endswith(".lock"):
            continue
        st = os.lstat(entry)
        out[entry.name] = (st.st_ino, st.st_size, st.st_mtime_ns, st.st_mode)
    return out


@pytest.mark.parametrize("damage", _DAMAGE)
def test_a_damaged_document_reads_as_the_last_good_copy_and_the_reader_writes_nothing(tmp_path, damage, caplog) -> None:
    """El que lee nunca escribe: devuelve la copia buena con una alerta y deja
    el disco como estaba. Lo repara el próximo que escriba (bajo el candado)."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps(_GOOD), encoding="utf-8")
    damage(path)
    before = _disk(path)
    caplog.set_level(logging.WARNING)

    assert FilesystemMetadataStore(tmp_path).read(SID) == _GOOD
    [alert] = _alerts(caplog)
    assert SID in alert.getMessage() and "última copia buena" in alert.getMessage()
    assert _disk(path) == before, "la lectura escribió"


def _torn_once(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Un script viejo escribe `metadata.json` a pedazos: la primera lectura
    sale cortada; la siguiente lo ve entero."""
    real_read_text = Path.read_text
    torn = {"left": 1}

    def read_text_while_being_written(self: Path, *args: Any, **kwargs: Any) -> str:
        text = real_read_text(self, *args, **kwargs)
        if self == path and torn["left"]:
            torn["left"] -= 1
            return text[: len(text) // 2]
        return text

    monkeypatch.setattr(Path, "read_text", read_text_while_being_written)


def test_a_torn_document_under_the_lock_is_damage_and_is_set_aside_whole(tmp_path, monkeypatch, caplog) -> None:
    """Sin reintentos ni esperas: ya no quedan escritores de afuera a medias
    (los scripts escriben con el store y el gate lo vigila). Si aun así quien
    escribe encuentra el archivo cortado, es daño: lo aparta UNA vez tal como
    está en disco y sigue desde la copia buena."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps({"tag": "VIEJO"}), encoding="utf-8")
    current = {**_GOOD, "tag": "ACTUAL"}
    path.write_text(json.dumps(current), encoding="utf-8")
    on_disk = path.read_bytes()
    _torn_once(monkeypatch, path)
    caplog.set_level(logging.WARNING)

    FilesystemMetadataStore(tmp_path).update(SID, lambda d: {**d, "n": 1})

    assert _json_or_none(path) == {"tag": "VIEJO", "n": 1}
    [copy] = _damaged_copies(path)
    assert copy.read_bytes() == on_disk, "lo apartado no es lo que había en disco"
    assert _alerts(caplog)


def test_a_torn_read_without_the_lock_gives_the_copy_and_writes_nothing(tmp_path, monkeypatch, caplog) -> None:
    """Quien solo lee no espera ni repara: ve la copia buena (con la alerta) y
    el archivo queda como estaba."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps({"tag": "VIEJO"}), encoding="utf-8")
    current = {**_GOOD, "tag": "ACTUAL"}
    path.write_text(json.dumps(current), encoding="utf-8")
    before = _disk(path)
    _torn_once(monkeypatch, path)
    caplog.set_level(logging.WARNING)

    assert FilesystemMetadataStore(tmp_path).read(SID) == {"tag": "VIEJO"}

    assert _disk(path) == before, "la lectura escribió"
    assert _alerts(caplog)


def test_a_missing_document_with_a_prev_reads_as_the_copy_and_the_next_write_restores_it(tmp_path, caplog) -> None:
    """«No existe pero hay `.prev`»: algo lo borró (nadie en `src/` borra
    `metadata.json` a propósito). La lectura devuelve la copia con alerta y NO
    lo recrea; lo recrea el próximo que escriba."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps(_GOOD), encoding="utf-8")
    caplog.set_level(logging.WARNING)
    store = FilesystemMetadataStore(tmp_path)

    assert store.read(SID) == _GOOD
    assert _alerts(caplog)
    assert not path.exists(), "la lectura recreó el documento"
    store.update(SID, lambda d: {**d, "last_inbound_message_id": "wamid.2"})
    assert json.loads(path.read_text(encoding="utf-8")) == {**_GOOD, "last_inbound_message_id": "wamid.2"}


def test_a_new_session_reads_empty_without_an_alert(tmp_path, caplog) -> None:
    caplog.set_level(logging.WARNING)
    store = FilesystemMetadataStore(tmp_path)

    assert store.read(SID) == {}
    store.update(SID, lambda d: {**d, "phone_number_id": "pnid-1"})
    assert store.read(SID) == {"phone_number_id": "pnid-1"}
    assert _alerts(caplog) == []


# --- escribir sobre un documento dañado -------------------------------------------


@pytest.mark.parametrize("damage", _DAMAGE)
def test_a_write_over_a_damaged_document_starts_from_the_last_good_copy(tmp_path, damage, caplog) -> None:
    """Sigue en humano con su pedido, y la conversación sigue escribiendo."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps(_GOOD), encoding="utf-8")
    damage(path)
    damaged_bytes = _bytes(path)
    damage(path)
    caplog.set_level(logging.WARNING)

    written = FilesystemMetadataStore(tmp_path).update(SID, lambda d: {**d, "last_inbound_message_id": "wamid.2"})

    expected = {**_GOOD, "last_inbound_message_id": "wamid.2"}
    assert written == expected
    assert json.loads(path.read_text(encoding="utf-8")) == expected
    copies = _damaged_copies(path)
    assert len(copies) == 1, "el dañado se aparta una vez"
    assert _bytes(copies[0]) == damaged_bytes
    assert _alerts(caplog)


def test_write_merged_over_a_damaged_document_merges_onto_the_last_good_copy(tmp_path) -> None:
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps(_GOOD), encoding="utf-8")
    _broken_json(path)
    base = {"active_route": "ventas", "tag": "NO_ETIQUETADO"}

    FilesystemMetadataStore(tmp_path).write_merged(SID, base=base, ours={**base, "last_inbound_message_id": "wamid.2"})

    assert json.loads(path.read_text(encoding="utf-8")) == {**_GOOD, "last_inbound_message_id": "wamid.2"}
    assert len(_damaged_copies(path)) == 1


def test_a_full_write_over_a_damaged_document_also_sets_it_aside(tmp_path) -> None:
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps(_GOOD), encoding="utf-8")
    _broken_json(path)

    FilesystemMetadataStore(tmp_path).write(SID, {"n": 1})

    assert json.loads(path.read_text(encoding="utf-8")) == {"n": 1}
    assert len(_damaged_copies(path)) == 1
    assert json.loads(_prev(path).read_text(encoding="utf-8")) == _GOOD, "no se rota desde un dañado"


def test_with_both_copies_damaged_it_starts_empty_sets_aside_and_keeps_writing(tmp_path, caplog) -> None:
    path = _session(tmp_path)
    _prev(path).write_text("{roto", encoding="utf-8")
    _broken_json(path)
    caplog.set_level(logging.WARNING)
    store = FilesystemMetadataStore(tmp_path)

    assert store.read(SID) == {}
    assert _alerts(caplog)
    assert store.update(SID, lambda d: {**d, "last_inbound_message_id": "wamid.2"}) == {
        "last_inbound_message_id": "wamid.2"
    }
    assert json.loads(path.read_text(encoding="utf-8")) == {"last_inbound_message_id": "wamid.2"}
    assert len(_damaged_copies(path)) == 1


def test_prev_is_not_rotated_from_a_damaged_document(tmp_path) -> None:
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps(_GOOD), encoding="utf-8")
    _broken_json(path)

    FilesystemMetadataStore(tmp_path).update(SID, lambda d: {**d, "n": 1})

    assert json.loads(_prev(path).read_text(encoding="utf-8")) == _GOOD, "se pisó la copia buena con la dañada"


@pytest.mark.parametrize("link_works", [True, False], ids=["apartado-con-enlace", "apartado-movido"])
def test_a_write_that_fails_after_setting_aside_leaves_the_document_in_place(
    tmp_path, monkeypatch, link_works
) -> None:
    """`metadata.json` nunca queda ausente: si la escritura falla, lo apartado
    vuelve a su lugar."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps(_GOOD), encoding="utf-8")
    _broken_json(path)
    damaged_bytes = path.read_bytes()

    def disk_full(*args: Any, **kwargs: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(state, "atomic_write_json", disk_full)
    if not link_works:
        monkeypatch.setattr(state.os, "link", disk_full)

    with pytest.raises(OSError):
        FilesystemMetadataStore(tmp_path).update(SID, lambda d: {**d, "n": 1})

    assert path.read_bytes() == damaged_bytes
    assert _damaged_copies(path) == []


# --- la copia buena ----------------------------------------------------------------


def test_a_healthy_write_keeps_the_previous_version_as_prev(tmp_path) -> None:
    store = FilesystemMetadataStore(tmp_path)
    store.update(SID, lambda d: {"v": 1})
    store.update(SID, lambda d: {**d, "v": 2})

    path = tmp_path / SID / "metadata.json"
    assert json.loads(_prev(path).read_text(encoding="utf-8")) == {"v": 1}
    assert json.loads(path.read_text(encoding="utf-8")) == {"v": 2}


def test_no_backup_piles_up_on_each_message(tmp_path) -> None:
    store = FilesystemMetadataStore(tmp_path)
    for n in range(5):
        store.update(SID, lambda d, n=n: {**d, "n": n})
    store.write_merged(SID, base={"n": 4}, ours={"n": 4, "m": 1})

    files = sorted(p.name for p in (tmp_path / SID).iterdir())
    assert files == ["metadata.json", "metadata.json.lock", "metadata.json.prev"]


def test_the_recovered_copy_is_one_write_behind(tmp_path) -> None:
    """N-1, a propósito: `.prev` es la versión ANTERIOR a la última escritura.
    Un daño justo después de escribir PIERDE esa última escritura (p. ej. la
    toma del operador): la próxima escritura va encima de la copia y no la
    trae de vuelta; hay que volver a hacerla."""
    store = FilesystemMetadataStore(tmp_path)
    store.update(SID, lambda d: {"active_route": "ventas"})
    store.update(SID, lambda d: {**d, "active_route": "humano"})
    _broken_json(tmp_path / SID / "metadata.json")

    assert store.read(SID) == {"active_route": "ventas"}
    store.update(SID, lambda d: {**d, "n": 1})
    assert store.read(SID) == {"active_route": "ventas", "n": 1}, "la toma no vuelve con la próxima escritura"


# --- segunda revisión de la recuperación (1b6f9b83) ----------------------------


def _json_or_none(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _failing_reads(monkeypatch: pytest.MonkeyPatch, names: set[str], times: int, code: int = errno.EMFILE) -> None:
    """Las primeras `times` lecturas de esos archivos fallan con un error
    PASAJERO del sistema (no del archivo): EMFILE, EIO…"""
    real_read_text = Path.read_text
    left = {"n": times}

    def read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name in names and left["n"] > 0:
            left["n"] -= 1
            raise OSError(code, os.strerror(code))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)


@pytest.mark.parametrize("times", [1, 2])
@pytest.mark.parametrize("how", ["update", "write_merged"])
def test_a_transient_read_error_under_the_lock_is_retried_and_the_fresh_document_is_written(
    tmp_path, monkeypatch, times, how
) -> None:
    """Octava revisión: quien escribe reintenta (bajo el candado, esperas
    cortas) un error pasajero al releer. Escribe sobre el documento FRESCO —la
    toma del humano—, nunca sobre la copia vieja, y la copia buena rota."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps({**_GOOD, "active_route": "ventas"}), encoding="utf-8")
    path.write_text(json.dumps(_GOOD), encoding="utf-8")
    on_disk = path.read_bytes()
    _failing_reads(monkeypatch, {"metadata.json", "metadata.json.prev"}, times)
    store = FilesystemMetadataStore(tmp_path)

    if how == "update":
        store.update(SID, lambda d: {**d, "ui_intents_failures": [{"kind": "x"}]})
    else:
        store.write_merged(SID, base={}, ours={"ui_intents_failures": [{"kind": "x"}]})

    assert _json_or_none(path) == {**_GOOD, "ui_intents_failures": [{"kind": "x"}]}
    assert _prev(path).read_bytes() == on_disk
    assert _damaged_copies(path) == []


@pytest.mark.parametrize("times", [4, 5])
@pytest.mark.parametrize("how", ["update", "write_merged"])
def test_a_transient_read_error_is_not_damage_the_write_fails_and_nothing_changes(
    tmp_path, monkeypatch, times, how
) -> None:
    """Un documento SANO (con la toma de un humano como última escritura) no
    se aparta ni se reemplaza por la copia vieja porque el sistema se quedó
    sin descriptores: tras los 3 reintentos la escritura falla (Temporal
    reintenta la activity) y nada cambia."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps({**_GOOD, "active_route": "ventas"}), encoding="utf-8")
    path.write_text(json.dumps(_GOOD), encoding="utf-8")
    before, prev_before = path.read_bytes(), _prev(path).read_bytes()
    _failing_reads(monkeypatch, {"metadata.json", "metadata.json.prev"}, times)
    store = FilesystemMetadataStore(tmp_path)

    raised = None
    try:
        if how == "update":
            store.update(SID, lambda d: {**d, "ui_intents_failures": [{"kind": "x"}]})
        else:
            store.write_merged(SID, base={}, ours={"ui_intents_failures": [{"kind": "x"}]})
    except OSError as exc:
        raised = exc

    assert path.read_bytes() == before, "con un error pasajero, la escritura tocó el documento"
    assert _prev(path).read_bytes() == prev_before
    assert _damaged_copies(path) == []
    assert raised is not None and raised.errno == errno.EMFILE


def test_a_transient_error_reading_the_good_copy_also_stops_the_write(tmp_path, monkeypatch) -> None:
    """Dañado de verdad, pero la copia buena no se pudo leer por un error
    pasajero: no se sigue desde `{}` (perdería la sesión); falla y no toca nada."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps(_GOOD), encoding="utf-8")
    _broken_json(path)
    before = path.read_bytes()
    _failing_reads(monkeypatch, {"metadata.json.prev"}, 4)  # la lectura y sus 3 reintentos

    raised = None
    try:
        FilesystemMetadataStore(tmp_path).update(SID, lambda d: {**d, "n": 1})
    except OSError as exc:
        raised = exc

    assert path.read_bytes() == before, "siguió desde vacío y pisó la sesión"
    assert _damaged_copies(path) == []
    assert raised is not None and raised.errno == errno.EMFILE


def _write_fails(monkeypatch: pytest.MonkeyPatch, times: int, code: int = errno.EIO) -> list[float]:
    """Las primeras `times` escrituras (`atomic_write_json`) fallan con un error
    pasajero; devuelve las esperas que hizo quien escribe."""
    real_write = state.atomic_write_json
    left = {"n": times}

    def flaky_write(path: Path, data: Any) -> None:
        if left["n"]:
            left["n"] -= 1
            raise OSError(code, os.strerror(code))
        real_write(path, data)

    sleeps: list[float] = []
    monkeypatch.setattr(state, "atomic_write_json", flaky_write)
    monkeypatch.setattr(state.time, "sleep", sleeps.append)
    return sleeps


@pytest.mark.parametrize("times", [1, 2])
def test_a_transient_error_writing_is_retried_with_short_waits(tmp_path, monkeypatch, times) -> None:
    store = FilesystemMetadataStore(tmp_path)
    store.write(SID, {"v": 1})
    sleeps = _write_fails(monkeypatch, times)

    store.update(SID, lambda d: {**d, "v": 2})

    path = tmp_path / SID / "metadata.json"
    assert _json_or_none(path) == {"v": 2}, "un error pasajero de un instante hizo fallar la escritura"
    assert _json_or_none(_prev(path)) == {"v": 1}
    assert sleeps == [0.02, 0.06][:times]


def test_a_transient_error_writing_four_times_raises_and_leaves_the_disk_intact(tmp_path, monkeypatch) -> None:
    store = FilesystemMetadataStore(tmp_path)
    store.write(SID, {"v": 1})
    store.write(SID, {"v": 2})
    path = tmp_path / SID / "metadata.json"
    before = _disk(path)
    _write_fails(monkeypatch, 4)

    with pytest.raises(OSError) as raised:
        store.update(SID, lambda d: {**d, "v": 3})

    assert raised.value.errno == errno.EIO
    assert _disk(path) == before, "una escritura que falló tocó el disco"


@pytest.mark.parametrize("failure", ["disco-lleno", "no-serializable"])
def test_a_write_that_fails_does_not_rotate_the_good_copy(tmp_path, monkeypatch, failure) -> None:
    """`.prev` se rota DESPUÉS de escribir: si la escritura falla, la copia
    buena queda como estaba y en otro inodo (si fueran el mismo archivo, un
    daño en el lugar se llevaría los dos)."""
    store = FilesystemMetadataStore(tmp_path)
    store.write(SID, {"v": 1})
    store.write(SID, {"v": 2})
    path = tmp_path / SID / "metadata.json"
    prev_before = _prev(path).read_bytes()
    if failure == "disco-lleno":

        def disk_full(*args: Any, **kwargs: Any) -> None:
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(state, "atomic_write_json", disk_full)
        mutator = lambda d: {**d, "v": 3}  # noqa: E731
    else:
        mutator = lambda d: {**d, "v": object()}  # noqa: E731

    with pytest.raises((OSError, TypeError)):
        store.update(SID, mutator)

    assert _prev(path).read_bytes() == prev_before, "la copia buena rotó con una escritura que falló"
    assert os.stat(path).st_ino != os.stat(_prev(path)).st_ino


@pytest.mark.parametrize("how", ["read", "update"])
def test_nothing_sleeps_or_starts_a_thread_over_a_damaged_document(tmp_path, monkeypatch, how) -> None:
    """`read()` corre en el bucle async (ingest, router, bandeja) y nadie
    repara en segundo plano: ni esperas ni hilos. Quien escribe tampoco
    reintenta con esperas (bajo el candado hay un solo escritor y todos
    escriben atómico)."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps(_GOOD), encoding="utf-8")
    _broken_json(path)
    sleeps: list[float] = []
    threads: list[str] = []
    real_start = threading.Thread.start

    def start(self: threading.Thread) -> None:
        threads.append(self.name)
        real_start(self)

    monkeypatch.setattr(state.time, "sleep", sleeps.append)
    monkeypatch.setattr(threading.Thread, "start", start)
    store = FilesystemMetadataStore(tmp_path)

    if how == "read":
        assert store.read(SID) == _GOOD
    else:
        store.update(SID, lambda d: {**d, "n": 1})

    assert sleeps == [], f"{how}() esperó"
    assert threads == [], f"{how}() arrancó un hilo"


def test_the_next_writer_repairs_what_a_reader_found_damaged(tmp_path) -> None:
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps(_GOOD), encoding="utf-8")
    _broken_json(path)
    damaged_bytes = path.read_bytes()
    store = FilesystemMetadataStore(tmp_path)

    assert store.read(SID) == _GOOD
    assert path.read_bytes() == damaged_bytes, "la lectura escribió"
    assert _damaged_copies(path) == []

    store.update(SID, lambda d: {**d, "n": 1})

    assert _json_or_none(path) == {**_GOOD, "n": 1}
    [copy] = _damaged_copies(path)
    assert copy.read_bytes() == damaged_bytes


def test_the_alert_fires_once_per_damage_episode(tmp_path, caplog) -> None:
    """Nadie repara al leer, así que el daño sigue hasta la próxima escritura:
    las lecturas no repiten la alerta. Otro daño después de reparar, sí."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps(_GOOD), encoding="utf-8")
    _broken_json(path)
    caplog.set_level(logging.WARNING)
    store = FilesystemMetadataStore(tmp_path)

    for _ in range(3):
        assert store.read(SID) == _GOOD
    assert len(_alerts(caplog)) == 1, "una alerta por lectura"

    store.update(SID, lambda d: {**d, "n": 1})  # repara
    _empty_file(path)  # otro daño: otro episodio
    store.read(SID)
    assert len(_alerts(caplog)) == 2


@pytest.mark.parametrize("case", ["doc", "copia-de-un-danado", "copia-de-uno-ausente"])
def test_a_transient_read_error_makes_read_raise(tmp_path, monkeypatch, case) -> None:
    """El que lee nunca recibe una copia vieja ni `{}` por un error pasajero:
    lanza. Así quien lee → decide → escribe no decide sobre algo que no pudo
    leer (las redes de cierre pisaron la toma de un operador con un EMFILE)."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps({**_GOOD, "active_route": "ventas"}), encoding="utf-8")
    if case == "doc":
        path.write_text(json.dumps(_GOOD), encoding="utf-8")
        failing = {"metadata.json"}
    elif case == "copia-de-un-danado":
        _broken_json(path)
        failing = {"metadata.json.prev"}
    else:
        failing = {"metadata.json.prev"}
    _failing_reads(monkeypatch, failing, 1)

    with pytest.raises(OSError) as raised:
        FilesystemMetadataStore(tmp_path).read(SID)

    assert raised.value.errno == errno.EMFILE


def test_a_reader_racing_a_brand_new_session_does_not_fall_back_to_the_copy(tmp_path, monkeypatch, caplog) -> None:
    """El lector (sin candado) ve `metadata.json` ausente y, antes de mirar
    `.prev`, el escritor hace dos escrituras: no es «algo lo borró»; se relee
    el documento antes de caer a la copia."""
    store = FilesystemMetadataStore(tmp_path)
    writer = FilesystemMetadataStore(tmp_path)
    real_load = state._load
    raced: list[bool] = []

    def racing_load(path: Path) -> Any:
        result = real_load(path)
        if path.name == "metadata.json" and result is state._ABSENT and not raced:
            raced.append(True)
            writer.update(SID, lambda d: {"n": 1})
            writer.update(SID, lambda d: {**d, "n": 2})
        return result

    monkeypatch.setattr(state, "_load", racing_load)
    caplog.set_level(logging.WARNING)

    assert store.read(SID) == {"n": 2}
    assert _alerts(caplog) == []


def _refuse(*args: Any, **kwargs: Any) -> None:
    raise PermissionError(errno.EPERM, "Operation not permitted")


def test_without_hard_links_the_good_copy_is_copied(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(state.os, "link", _refuse)
    store = FilesystemMetadataStore(tmp_path)
    store.update(SID, lambda d: {"v": 1})
    store.update(SID, lambda d: {**d, "v": 2})

    path = tmp_path / SID / "metadata.json"
    assert _json_or_none(_prev(path)) == {"v": 1}
    assert os.stat(path).st_ino != os.stat(_prev(path)).st_ino


def test_with_neither_link_nor_copy_the_write_still_happens(tmp_path, monkeypatch, caplog) -> None:
    store = FilesystemMetadataStore(tmp_path)
    store.update(SID, lambda d: {"v": 1})
    store.update(SID, lambda d: {**d, "v": 2})
    monkeypatch.setattr(state.os, "link", _refuse)
    monkeypatch.setattr(state.shutil, "copy2", _refuse)
    caplog.set_level(logging.WARNING)

    store.update(SID, lambda d: {**d, "v": 3})

    path = tmp_path / SID / "metadata.json"
    assert _json_or_none(path) == {"v": 3}
    assert _json_or_none(_prev(path)) == {"v": 1}, "la copia vieja queda como estaba"
    assert "metadata_prev_not_kept" in caplog.text
    assert not list(path.parent.glob(".metadata.json.prev*")), "quedó un temporal suelto"


def test_a_writer_killed_before_promoting_the_copy_leaves_no_pile_of_temps(tmp_path, monkeypatch) -> None:
    """Un SIGKILL entre escribir y promover la copia deja el temporal de la
    versión vieja. Con un nombre FIJO por sesión (bajo el candado hay un solo
    escritor) no se acumulan: la próxima escritura lo reemplaza y lo promueve."""
    store = FilesystemMetadataStore(tmp_path)
    store.update(SID, lambda d: {"v": 1})
    path = tmp_path / SID / "metadata.json"
    with monkeypatch.context() as killed:
        killed.setattr(state, "_promote_previous", lambda tmp, target: None)
        for v in (2, 3, 4):
            store.update(SID, lambda d, v=v: {**d, "v": v})

    leftovers = [p.name for p in path.parent.iterdir() if p.name.startswith(".")]
    assert len(leftovers) <= 1, f"se acumulan temporales: {leftovers}"

    store.update(SID, lambda d: {**d, "v": 5})

    assert sorted(p.name for p in path.parent.iterdir()) == ["metadata.json", "metadata.json.lock", "metadata.json.prev"]
    assert _json_or_none(_prev(path)) == {"v": 4}


def test_a_directory_in_place_of_the_document_is_damage(tmp_path) -> None:
    """EISDIR (o sin permiso) es daño del archivo, no un error pasajero: se
    recupera de la copia buena."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps(_GOOD), encoding="utf-8")
    path.mkdir()
    store = FilesystemMetadataStore(tmp_path)

    store.update(SID, lambda d: {**d, "n": 1})

    assert _json_or_none(path) == {**_GOOD, "n": 1}
    assert len(_damaged_copies(path)) == 1
