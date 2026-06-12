"""Tests del append-mode de `pending_handoff_summary` (M2/L-13).

Runs 3607aecc + 8894825b: cada write PISABA al anterior — si el workflow
target aún no había leído, el contexto previo se perdía (el "Dame 3" del
cliente murió así, pisado por la autotransferencia 2s después). Append-mode:
múltiples writes antes de una lectura se ACUMULAN con ``\\n``; el
``read_and_clear`` devuelve el blob completo y limpia.

Cubre:
  * write sobre vacío → summary solo.
  * write x2 distintos → ambos, en orden de llegada.
  * write duplicado exacto → no duplica (idempotencia ante retry de activity).
  * round-trip con read_and_clear: lee blob completo, limpia, y el próximo
    write arranca fresco.
"""
from __future__ import annotations

import json

import pytest
from temporalio.testing import ActivityEnvironment

from src.platform.temporal.dispatcher import write_pending_handoff_activity


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Redirige el vault del dispatcher (y del reader de sales) a un tmp."""
    monkeypatch.setattr(
        "src.platform.temporal.dispatcher.WORKSPACE_VAULT_DIR", tmp_path
    )
    monkeypatch.setattr(
        "src.plugins.chats.agent.sales.activities.bootstrap_session.WORKSPACE_VAULT_DIR",
        tmp_path,
    )
    return tmp_path


def _read_metadata(vault, session_id: str) -> dict:
    return json.loads((vault / session_id / "metadata.json").read_text())


async def test_first_write_sets_summary(vault) -> None:
    env = ActivityEnvironment()
    await env.run(write_pending_handoff_activity, "wa_m2a", "primer handoff")

    data = _read_metadata(vault, "wa_m2a")
    assert data["pending_handoff_summary"] == "primer handoff"


async def test_second_write_appends_not_overwrites(vault) -> None:
    """El caso del run: handoff de transferencia + 'Usuario respondió: Dame 2'
    escritos antes de que sales lea — ambos deben sobrevivir."""
    env = ActivityEnvironment()
    await env.run(
        write_pending_handoff_activity, "wa_m2b", "Cliente respondió 'A si'"
    )
    await env.run(
        write_pending_handoff_activity, "wa_m2b", "Usuario respondió: Dame 2"
    )

    data = _read_metadata(vault, "wa_m2b")
    assert data["pending_handoff_summary"] == (
        "Cliente respondió 'A si'\nUsuario respondió: Dame 2"
    )


async def test_exact_duplicate_write_is_idempotent(vault) -> None:
    """Retry de la activity tras un write exitoso sin ack → no duplica."""
    env = ActivityEnvironment()
    await env.run(write_pending_handoff_activity, "wa_m2c", "mismo summary")
    await env.run(write_pending_handoff_activity, "wa_m2c", "mismo summary")

    data = _read_metadata(vault, "wa_m2c")
    assert data["pending_handoff_summary"] == "mismo summary"


async def test_roundtrip_with_read_and_clear(vault) -> None:
    """read_and_clear devuelve el blob acumulado completo y limpia; el
    siguiente write arranca fresco (no re-appendea sobre lo consumido)."""
    from src.plugins.chats.agent.sales.activities import (
        read_and_clear_pending_handoff_activity,
    )

    env = ActivityEnvironment()
    await env.run(write_pending_handoff_activity, "wa_m2d", "handoff A")
    await env.run(write_pending_handoff_activity, "wa_m2d", "handoff B")

    blob = await env.run(read_and_clear_pending_handoff_activity, "wa_m2d")
    assert blob == "handoff A\nhandoff B"

    second = await env.run(read_and_clear_pending_handoff_activity, "wa_m2d")
    assert second is None

    await env.run(write_pending_handoff_activity, "wa_m2d", "handoff C")
    data = _read_metadata(vault, "wa_m2d")
    assert data["pending_handoff_summary"] == "handoff C"
