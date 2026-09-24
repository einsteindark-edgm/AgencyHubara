"""El envío free-form devuelve lo que entregó (traza v2, plan del laboratorio PR 2).

La traza del turno registra cada burbuja con su wamid: es lo que el modal del
hilo muestra en el paso "Workflow → Cliente" y lo que permite unir una cita
del cliente con el paso que la produjo. Antes la activity devolvía `None`.

También cierra el hallazgo lateral del plan (§10): la despedida que sale
justo después de que una tool cerró el episodio no quedaba en
`outbound_messages` de ese episodio (el costo de esa burbuja se perdía).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment

from src.platform.whatsapp import activities as wa_activities
from src.platform.whatsapp.activities import send_whatsapp_message_activity
from src.platform.whatsapp.dtos import OutboundResult

SESSION = "wa_573001234567"


@pytest.fixture(autouse=True)
def _fast_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE_TEST")

    async def _no_sleep(_secs):
        return None

    monkeypatch.setattr(wa_activities.asyncio, "sleep", _no_sleep)


@pytest.fixture
def meta(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def fake_send(phone_number_id: str, to: str, text: str, reply_to_message_id=None):
        calls.append(text)
        return OutboundResult(wa_message_id=f"wamid.{len(calls)}", ok=True)

    monkeypatch.setattr(wa_activities.whatsapp_client, "send_text", fake_send)
    return calls


def _seed(vault: Path, episode: dict) -> None:
    d = vault / SESSION
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(
        json.dumps({"phone_number_id": "PHONE_TEST", "episodes": [episode]}), encoding="utf-8"
    )


def _episode(vault: Path) -> dict:
    return json.loads((vault / SESSION / "metadata.json").read_text(encoding="utf-8"))["episodes"][-1]


async def _send(message: str):
    return await ActivityEnvironment().run(send_whatsapp_message_activity, SESSION, message)


@pytest.mark.asyncio
async def test_activity_returns_each_delivered_bubble_with_its_wamid(_isolate_vault_dir: Path, meta) -> None:
    _seed(_isolate_vault_dir, {"episode_id": "ep_001", "closed_at_ms": None})

    delivered = await _send("¡Hola!\n\n¿En qué te ayudo?")

    assert delivered == [
        {"wamid": "wamid.1", "text": "¡Hola!"},
        {"wamid": "wamid.2", "text": "¿En qué te ayudo?"},
    ]


@pytest.mark.asyncio
async def test_blocked_admin_text_delivers_nothing(_isolate_vault_dir: Path, meta) -> None:
    _seed(_isolate_vault_dir, {"episode_id": "ep_001", "closed_at_ms": None})

    delivered = await _send("La conversación queda etiquetada como `INTERESADO`.")

    assert delivered == []
    assert meta == []


@pytest.mark.asyncio
async def test_farewell_right_after_the_episode_closed_is_logged_in_that_episode(
    _isolate_vault_dir: Path, meta
) -> None:
    closed_ms = int(time.time() * 1000) - 5_000  # la tool cerró el episodio hace 5 s
    _seed(_isolate_vault_dir, {"episode_id": "ep_001", "closed_at_ms": closed_ms})

    await _send("Listo, tu pedido quedó registrado 🤍")

    assert [m["wa_message_id"] for m in _episode(_isolate_vault_dir)["outbound_messages"]] == ["wamid.1"]


@pytest.mark.asyncio
async def test_message_long_after_the_close_does_not_touch_the_old_episode(
    _isolate_vault_dir: Path, meta
) -> None:
    closed_ms = int(time.time() * 1000) - 60 * 60 * 1000
    _seed(_isolate_vault_dir, {"episode_id": "ep_001", "closed_at_ms": closed_ms})

    await _send("Hola de nuevo")

    assert "outbound_messages" not in _episode(_isolate_vault_dir)


@pytest.mark.asyncio
async def test_retry_that_hits_the_duplicate_guard_reports_the_text_as_delivered(
    _isolate_vault_dir: Path, meta
) -> None:
    """El primer intento envió y la activity se cayó antes de devolver: el
    reintento lo frena el anti doble-toque. Salió, aunque no sepamos el wamid."""
    _seed(_isolate_vault_dir, {"episode_id": "ep_001", "closed_at_ms": None})
    await _send("¿Te muestro el catálogo?")

    again = await _send("¿Te muestro el catálogo?")

    assert again == [{"wamid": "", "text": "¿Te muestro el catálogo?"}]
    assert meta == ["¿Te muestro el catálogo?"]
