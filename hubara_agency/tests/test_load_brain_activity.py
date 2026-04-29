"""Tests de `load_remarketing_brain_activity` (F6.3).

Saca del workflow Remarketing la lectura de filesystem (`Path.read_text`) que
ADR-006 dejo como deuda consciente.

Cubierto:
  * La activity ejecuta `load_brain` sobre `REMARKETING_BRAIN_DIR` y devuelve
    `list[str]`.
  * Con un brain falso (tmp_path con `identity.md` etc.), retorna los contenidos
    en orden.
  * Si la dir no tiene los archivos esperados, retorna `[]` sin fallar (mismo
    comportamiento que `core.brains.load_brain`).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment


async def test_load_brain_activity_returns_list_of_strings_from_real_dir() -> None:
    """Smoke: usa el shared_brain real del dominio Remarketing."""
    from src.domains.remarketing_whatsapp.activities import (
        load_remarketing_brain_activity,
    )

    env = ActivityEnvironment()
    result = await env.run(load_remarketing_brain_activity)

    assert isinstance(result, list)
    assert all(isinstance(item, str) for item in result)
    # El shared_brain real tiene 3 archivos (identity, knowledge, instructions).
    assert len(result) == 3
    # Cada uno debe contener algo (no estan vacios).
    assert all(item.strip() for item in result)


async def test_load_brain_with_synthetic_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verifica el contrato sobre un brain falso: 3 archivos, orden estable."""
    fake_brain = tmp_path / "shared_brain"
    fake_brain.mkdir()
    (fake_brain / "identity.md").write_text("IDENTITY_BODY", encoding="utf-8")
    (fake_brain / "knowledge.md").write_text("KNOWLEDGE_BODY", encoding="utf-8")
    (fake_brain / "instructions.md").write_text("INSTRUCTIONS_BODY", encoding="utf-8")

    # Reemplazamos la dir global para que la activity use el brain sintetico.
    monkeypatch.setattr(
        "src.domains.remarketing_whatsapp.activities.REMARKETING_BRAIN_DIR",
        fake_brain,
    )

    from src.domains.remarketing_whatsapp.activities import (
        load_remarketing_brain_activity,
    )

    env = ActivityEnvironment()
    result = await env.run(load_remarketing_brain_activity)

    assert result == ["IDENTITY_BODY", "KNOWLEDGE_BODY", "INSTRUCTIONS_BODY"]


async def test_load_brain_with_missing_files_returns_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    empty_brain = tmp_path / "no_brain"
    empty_brain.mkdir()

    monkeypatch.setattr(
        "src.domains.remarketing_whatsapp.activities.REMARKETING_BRAIN_DIR",
        empty_brain,
    )

    from src.domains.remarketing_whatsapp.activities import (
        load_remarketing_brain_activity,
    )

    env = ActivityEnvironment()
    result = await env.run(load_remarketing_brain_activity)

    assert result == []
