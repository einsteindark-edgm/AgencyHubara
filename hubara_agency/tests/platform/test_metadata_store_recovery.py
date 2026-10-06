"""Recuperación automática de un `metadata.json` dañado (decisión del operador,
2026-10-06, PR #393).

Un archivo dañado es un problema técnico: NO pasa la conversación al equipo
humano ni traba escrituras. El store se recupera solo, sin que ningún llamador
cambie:

  * cada escritura sobre un documento que se leyó bien deja la versión
    anterior como `metadata.json.prev` (la última copia buena);
  * una lectura dañada devuelve `.prev` con un log de ERROR (una vez por
    episodio de daño) y deja la reparación a un hilo aparte, bajo el candado;
  * una escritura sobre un documento dañado lo aparta UNA vez como
    `metadata.json.damaged-<ms>`, aplica el cambio sobre la copia recuperada
    (`.prev` o `{}`) y escribe: nunca deja de escribir;
  * un error de lectura PASAJERO (EMFILE, EIO…) no es daño: la escritura
    falla y no toca nada (Temporal reintenta).

La copia buena va una escritura atrás (N-1).
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


@pytest.fixture(autouse=True)
def _repair_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    """La reparación que dispara una lectura corre en un hilo aparte; acá, en
    el mismo hilo, para poder mirar su resultado (las pruebas que miden el
    hilo de quien lee lo cambian)."""
    monkeypatch.setattr(state, "_run_in_background", lambda job: job(), raising=False)


# --- leer -----------------------------------------------------------------------


@pytest.mark.parametrize("damage", _DAMAGE)
def test_a_damaged_document_reads_as_the_last_good_copy_with_an_alert(tmp_path, damage, caplog) -> None:
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps(_GOOD), encoding="utf-8")
    damage(path)
    caplog.set_level(logging.WARNING)

    assert FilesystemMetadataStore(tmp_path).read(SID) == _GOOD
    [alert] = _alerts(caplog)
    assert SID in alert.getMessage() and "última copia buena" in alert.getMessage()


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


def test_a_torn_read_under_the_lock_is_resolved_by_retrying(tmp_path, monkeypatch, caplog) -> None:
    """Quien escribe relee con reintentos: trabaja sobre el documento entero
    (no sobre la copia vieja), sin apartar nada ni alertar."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps({"tag": "VIEJO"}), encoding="utf-8")
    current = {**_GOOD, "tag": "ACTUAL"}
    path.write_text(json.dumps(current), encoding="utf-8")
    _torn_once(monkeypatch, path)
    caplog.set_level(logging.WARNING)

    FilesystemMetadataStore(tmp_path).update(SID, lambda d: {**d, "n": 1})

    assert _json_or_none(path) == {**current, "n": 1}
    assert _damaged_copies(path) == []
    assert _alerts(caplog) == []


def test_a_torn_read_without_the_lock_gives_the_copy_but_raises_no_alarm(tmp_path, monkeypatch, caplog) -> None:
    """Quien solo lee no espera (bucle async): ve la copia buena un instante.
    La reparación relee bajo el candado, lo encuentra entero y no toca nada."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps({"tag": "VIEJO"}), encoding="utf-8")
    current = {**_GOOD, "tag": "ACTUAL"}
    path.write_text(json.dumps(current), encoding="utf-8")
    before = path.read_bytes()
    _torn_once(monkeypatch, path)
    caplog.set_level(logging.WARNING)

    assert FilesystemMetadataStore(tmp_path).read(SID) == {"tag": "VIEJO"}

    assert path.read_bytes() == before
    assert _damaged_copies(path) == []
    assert _alerts(caplog) == []


def test_a_missing_document_with_a_prev_is_recovered_with_an_alert(tmp_path, caplog) -> None:
    """«No existe pero hay `.prev`»: algo lo borró (nadie en `src/` borra
    `metadata.json` a propósito). Se recupera con alerta."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps(_GOOD), encoding="utf-8")
    caplog.set_level(logging.WARNING)
    store = FilesystemMetadataStore(tmp_path)

    assert store.read(SID) == _GOOD
    assert _alerts(caplog)
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


@pytest.mark.parametrize("times", [3, 4])
@pytest.mark.parametrize("how", ["update", "write_merged"])
def test_a_transient_read_error_is_not_damage_the_write_fails_and_nothing_changes(
    tmp_path, monkeypatch, times, how
) -> None:
    """Un documento SANO (con la toma de un humano como última escritura) no
    se aparta ni se reemplaza por la copia vieja porque el sistema se quedó
    sin descriptores un instante: la escritura falla (Temporal reintenta) y
    nada cambia."""
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

    assert path.read_bytes() == before, "un error pasajero pisó un documento sano"
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
    _failing_reads(monkeypatch, {"metadata.json.prev"}, 3)

    raised = None
    try:
        FilesystemMetadataStore(tmp_path).update(SID, lambda d: {**d, "n": 1})
    except OSError as exc:
        raised = exc

    assert path.read_bytes() == before, "siguió desde vacío y pisó la sesión"
    assert _damaged_copies(path) == []
    assert raised is not None and raised.errno == errno.EMFILE


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


def test_read_does_not_sleep_on_the_callers_thread(tmp_path, monkeypatch) -> None:
    """`read()` corre en el bucle async (ingest, router, bandeja): un dañado
    no lo frena con esperas. Los reintentos van en la reparación, aparte."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps(_GOOD), encoding="utf-8")
    _broken_json(path)
    sleeps: list[threading.Thread] = []
    monkeypatch.setattr(state.time, "sleep", lambda seconds: sleeps.append(threading.current_thread()))
    started: list[threading.Thread] = []

    def in_another_thread(job: Any) -> threading.Thread:
        thread = threading.Thread(target=job, daemon=True)
        started.append(thread)
        thread.start()
        return thread

    monkeypatch.setattr(state, "_run_in_background", in_another_thread, raising=False)

    assert FilesystemMetadataStore(tmp_path).read(SID) == _GOOD
    for thread in started:
        thread.join(5)
    assert threading.current_thread() not in sleeps, "read() esperó en el hilo de quien lee"


def test_reading_a_damaged_document_repairs_it_under_the_lock(tmp_path, monkeypatch) -> None:
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps(_GOOD), encoding="utf-8")
    _broken_json(path)
    damaged_bytes = path.read_bytes()
    monkeypatch.setattr(state, "_run_in_background", lambda job: job(), raising=False)

    assert FilesystemMetadataStore(tmp_path).read(SID) == _GOOD

    assert _json_or_none(path) == _GOOD, "la lectura no disparó la reparación"
    [copy] = _damaged_copies(path) or [None]
    assert copy is not None and copy.read_bytes() == damaged_bytes


def test_the_repair_leaves_alone_a_document_someone_is_still_writing(tmp_path, monkeypatch, caplog) -> None:
    """Un script de afuera que no escribe atómico y sigue escribiendo: la
    reparación lo ve cortado en cada reintento, pero CAMBIANDO entre uno y
    otro. No es un daño quieto: no alerta, no aparta nada ni escribe la copia
    vieja encima de lo que el script deja (la próxima lectura vuelve a mirar)."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps({"tag": "VIEJO"}), encoding="utf-8")
    _broken_json(path)
    pieces = iter(['{"tag": "NUEVO"', '{"tag": "NUEVO", "n": 1', '{"tag": "NUEVO", "n": 1, "x": '])

    def the_script_keeps_writing(seconds: float) -> None:
        path.write_text(next(pieces, '{"tag": "NUEVO", "n": 1, "x": 2'), encoding="utf-8")

    monkeypatch.setattr(state.time, "sleep", the_script_keeps_writing)
    caplog.set_level(logging.WARNING)

    assert FilesystemMetadataStore(tmp_path).read(SID) == {"tag": "VIEJO"}

    assert path.read_text(encoding="utf-8").startswith('{"tag": "NUEVO"'), "la reparación escribió la copia vieja encima"
    assert _damaged_copies(path) == []
    assert _alerts(caplog) == []


def test_the_alert_fires_once_per_damage_episode(tmp_path, monkeypatch, caplog) -> None:
    """Mientras el daño siga (aquí la reparación no logra escribir), las
    lecturas no repiten la alerta. Otro daño después de reparar, sí."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps(_GOOD), encoding="utf-8")
    _broken_json(path)
    caplog.set_level(logging.WARNING)
    store = FilesystemMetadataStore(tmp_path)

    def disk_full(*args: Any, **kwargs: Any) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    with monkeypatch.context() as patched:
        patched.setattr(state, "atomic_write_json", disk_full)
        for _ in range(3):
            assert store.read(SID) == _GOOD
    assert len(_alerts(caplog)) == 1, "una alerta por lectura hasta la próxima escritura"

    store.update(SID, lambda d: {**d, "n": 1})  # repara
    _empty_file(path)  # otro daño: otro episodio
    store.read(SID)
    assert len(_alerts(caplog)) == 2


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
