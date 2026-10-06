"""Recuperación automática de un `metadata.json` dañado (decisión del operador,
2026-10-06, PR #393).

Un archivo dañado es un problema técnico: NO pasa la conversación al equipo
humano ni traba escrituras. El store se recupera solo, sin que ningún llamador
cambie:

  * cada escritura sobre un documento que se leyó bien deja la versión
    anterior como `metadata.json.prev` (la última copia buena);
  * una lectura dañada se reintenta (un escritor de afuera a medias) y, si
    sigue dañada, devuelve `.prev` con un log de ERROR;
  * una escritura sobre un documento dañado lo aparta UNA vez como
    `metadata.json.damaged-<ms>`, aplica el cambio sobre la copia recuperada
    (`.prev` o `{}`) y escribe: nunca deja de escribir.
"""
from __future__ import annotations

import json
import logging
import os
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


@pytest.mark.parametrize("damage", _DAMAGE)
def test_a_damaged_document_reads_as_the_last_good_copy_with_an_alert(tmp_path, damage, caplog) -> None:
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps(_GOOD), encoding="utf-8")
    damage(path)
    caplog.set_level(logging.WARNING)

    assert FilesystemMetadataStore(tmp_path).read(SID) == _GOOD
    [alert] = _alerts(caplog)
    assert SID in alert.getMessage() and "última copia buena" in alert.getMessage()


def test_a_torn_read_from_a_non_atomic_writer_is_resolved_by_retrying(tmp_path, monkeypatch, caplog) -> None:
    """Un script viejo escribe `metadata.json` a pedazos: la primera lectura
    sale cortada; el reintento (unos milisegundos después) la ve entera. Ni
    copia vieja ni alerta."""
    path = _session(tmp_path)
    _prev(path).write_text(json.dumps({"tag": "VIEJO"}), encoding="utf-8")
    current = {**_GOOD, "tag": "ACTUAL"}
    path.write_text(json.dumps(current), encoding="utf-8")
    real_read_text = Path.read_text
    torn = {"left": 1}

    def read_text_while_being_written(self: Path, *args: Any, **kwargs: Any) -> str:
        text = real_read_text(self, *args, **kwargs)
        if self == path and torn["left"]:
            torn["left"] -= 1
            return text[: len(text) // 2]
        return text

    monkeypatch.setattr(Path, "read_text", read_text_while_being_written)
    caplog.set_level(logging.WARNING)

    assert FilesystemMetadataStore(tmp_path).read(SID) == current
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
