"""El almacén BSUID → conversación es best-effort y no se envenena.

Revisión del PR (2026-10-09): el write iba fuera del `try` — un disco lleno
(ya pasó en prod) tumbaba el webhook con 500 y dejaba el archivo VACÍO: el
BSUID nunca se volvía a anclar y el cliente terminaba en dos conversaciones.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.plugins.chats.agent.sales.sender_identity_store import FilesystemSenderIdentity

_USER = "CO9990000000000002"


def test_the_first_conversation_wins(tmp_path: Path) -> None:
    store = FilesystemSenderIdentity(tmp_path)

    assert store.remember(_USER, _USER) == _USER
    assert store.remember(_USER, "573001234567") == _USER
    assert store.known_address(_USER) == _USER


def test_a_failed_write_does_not_raise_and_leaves_nothing_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = FilesystemSenderIdentity(tmp_path)

    def disk_full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "link", disk_full)
    assert store.remember(_USER, "573001234567") == "573001234567"
    monkeypatch.undo()

    assert store.known_address(_USER) is None
    assert store.remember(_USER, _USER) == _USER  # el siguiente mensaje sí ancla


def test_an_empty_file_left_by_a_crash_is_treated_as_absent(tmp_path: Path) -> None:
    (tmp_path / _USER).write_text("", encoding="utf-8")
    store = FilesystemSenderIdentity(tmp_path)

    assert store.known_address(_USER) is None
    assert store.remember(_USER, _USER) == _USER
    assert store.known_address(_USER) == _USER


def test_a_stored_value_that_is_not_a_conversation_address_is_ignored(tmp_path: Path) -> None:
    """El valor termina siendo `session_id` = path del vault: se revalida."""
    (tmp_path / _USER).write_text("../../etc", encoding="utf-8")

    assert FilesystemSenderIdentity(tmp_path).known_address(_USER) is None
