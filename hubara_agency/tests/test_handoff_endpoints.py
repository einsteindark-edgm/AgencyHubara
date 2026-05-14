"""Tests de los endpoints del dashboard handoff (`src/dashboard/handoff.py`).

Cubren los 3 endpoints:

  * `POST /api/dashboard/sessions/{id}/intervene` — setea ruta humano +
    termina workflows en vuelo.
  * `POST /api/dashboard/sessions/{id}/messages` — manda al cliente y persiste
    con `sender=human` (gate: requiere ruta humano).
  * `POST /api/dashboard/sessions/{id}/return-to-bot` — devuelve a ventas o
    remarketing; para remarketing arranca el workflow proactivamente.

Mockeamos:
  - `get_temporal_client` para evitar conexión real a Temporal.
  - `send_message_to_session` para evitar la llamada HTTP a WhatsApp Cloud.
  - `start_remarketing_for_session` y `terminate_session_workflows` con fakes
    que graban las llamadas.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_and_vault(tmp_path, monkeypatch):
    """TestClient con WORKSPACE_VAULT_DIR apuntando a tmp_path.

    Patcheamos el símbolo en cada módulo donde se usa (los stores leen
    `WORKSPACE_VAULT_DIR` import-time via `composition.py`, y `dashboard/api.py`
    también lo importa). Adicionalmente reseteamos los singletons del
    composition para que tomen la ruta nueva.
    """
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "test_phone")
    with (
        patch("src.dashboard.api.WORKSPACE_VAULT_DIR", tmp_path),
        patch("src.dashboard.composition.WORKSPACE_VAULT_DIR", tmp_path),
        patch("src.platform.session_history.store.FilesystemMessageHistoryStore.__init__", lambda self, vault_dir: setattr(self, "_vault_dir", tmp_path)),
    ):
        # Forzar reset del singleton del composition (en caso de tests previos).
        import src.dashboard.composition as comp
        comp._METADATA_STORE = None
        comp._HISTORY_STORE = None
        from src.main import app
        yield TestClient(app), tmp_path


def _write_metadata(vault, session_id: str, data: dict) -> None:
    d = vault / session_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


def _read_metadata(vault, session_id: str) -> dict:
    p = vault / session_id / "metadata.json"
    return json.loads(p.read_text(encoding="utf-8"))


# ---------- /intervene ----------


def test_intervene_sets_humano_route_and_terminates_running_workflows(
    client_and_vault,
):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_1", {"active_route": "ventas", "tag": "INTERESADO"})

    terminate_mock = AsyncMock(return_value=["session-wa_1"])
    with (
        patch(
            "src.dashboard.handoff.get_temporal_client",
            new=AsyncMock(return_value=object()),
        ),
        patch(
            "src.dashboard.handoff.terminate_session_workflows",
            new=terminate_mock,
        ),
    ):
        res = client.post(
            "/api/dashboard/sessions/wa_1/intervene",
            json={"motivo": "cliente pidió ayuda directa"},
        )

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["active_route"] == "humano"
    assert body["tag"] == "HUMANO"
    assert body["terminated_workflows"] == ["session-wa_1"]

    # metadata.json actualizado en disco
    data = _read_metadata(vault, "wa_1")
    assert data["active_route"] == "humano"
    assert data["tag"] == "HUMANO"
    assert data["motivo"] == "cliente pidió ayuda directa"
    # status_history apendeada
    assert len(data["status_history"]) == 1
    entry = data["status_history"][0]
    assert entry["tag"] == "HUMANO"
    assert entry["source"] == "dashboard_intervene"

    # Y se llamó al terminator
    terminate_mock.assert_awaited_once()


def test_intervene_uses_default_motivo_when_omitted(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_2", {})

    with (
        patch(
            "src.dashboard.handoff.get_temporal_client",
            new=AsyncMock(return_value=object()),
        ),
        patch(
            "src.dashboard.handoff.terminate_session_workflows",
            new=AsyncMock(return_value=[]),
        ),
    ):
        res = client.post(
            "/api/dashboard/sessions/wa_2/intervene",
            json={},
        )

    assert res.status_code == 200
    data = _read_metadata(vault, "wa_2")
    assert "dashboard" in data["motivo"].lower()


def test_intervene_still_succeeds_if_temporal_unreachable(client_and_vault):
    """Si get_temporal_client o terminate fallan, intervene NO 500-a:
    la metadata se persiste (que es lo crítico) y el endpoint reporta
    `terminated_workflows=[]`. El operador queda en humano y el siguiente
    webhook del cliente ya queda filtrado por LoadOrStartSalesSession.

    Este test es el guardrail anti-regresión del fix pre-mortem."""
    client, vault = client_and_vault
    _write_metadata(vault, "wa_unreachable", {"active_route": "ventas"})

    # get_temporal_client lanza ConnectionRefusedError simulando Temporal caído.
    boom = AsyncMock(side_effect=ConnectionRefusedError("cluster down"))
    with patch("src.dashboard.handoff.get_temporal_client", new=boom):
        res = client.post(
            "/api/dashboard/sessions/wa_unreachable/intervene",
            json={"motivo": "tomé control"},
        )

    # 200, no 500 — la metadata se persistió aunque temporal estuviera caído.
    assert res.status_code == 200
    body = res.json()
    assert body["active_route"] == "humano"
    assert body["terminated_workflows"] == []

    data = _read_metadata(vault, "wa_unreachable")
    assert data["active_route"] == "humano"
    assert data["tag"] == "HUMANO"


# ---------- /messages ----------


def test_send_message_persists_and_returns_when_route_humano(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_3", {"active_route": "humano", "tag": "HUMANO"})

    send_mock = AsyncMock()
    with patch("src.dashboard.handoff.send_message_to_session", new=send_mock):
        res = client.post(
            "/api/dashboard/sessions/wa_3/messages",
            json={"text": "Hola, soy del equipo Hubara 🤍"},
        )

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["sender"] == "human"
    assert body["content"] == "Hola, soy del equipo Hubara 🤍"

    # WhatsApp send fue llamado con el texto correcto.
    send_mock.assert_awaited_once_with("wa_3", "Hola, soy del equipo Hubara 🤍")

    # JSONL tiene un evento humano persistido.
    log = vault / "wa_3" / "sessions" / "wa_3.jsonl"
    assert log.exists()
    parsed = json.loads(log.read_text(encoding="utf-8").strip())
    assert parsed["role"] == "assistant"
    assert parsed["sender"] == "human"
    assert parsed["content"] == "Hola, soy del equipo Hubara 🤍"


def test_send_message_rejects_when_route_is_not_humano(client_and_vault):
    """Si la sesión está en ventas o remarketing, devolver 409 para evitar
    respuestas concurrentes (humano + bot simultáneos)."""
    client, vault = client_and_vault
    _write_metadata(vault, "wa_4", {"active_route": "ventas"})

    with patch("src.dashboard.handoff.send_message_to_session", new=AsyncMock()):
        res = client.post(
            "/api/dashboard/sessions/wa_4/messages",
            json={"text": "no debería poder"},
        )
    assert res.status_code == 409
    detail = res.json()["detail"].lower()
    assert "humano" in detail


def test_send_message_validates_text_not_empty(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_5", {"active_route": "humano"})

    res = client.post("/api/dashboard/sessions/wa_5/messages", json={"text": ""})
    assert res.status_code == 422  # pydantic min_length


# ---------- /return-to-bot ----------


def test_return_to_bot_ventas_only_marks_metadata(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(
        vault,
        "wa_6",
        {"active_route": "humano", "tag": "HUMANO", "motivo": "tomé control"},
    )

    start_remarketing = AsyncMock()
    with (
        patch(
            "src.dashboard.handoff.start_remarketing_for_session",
            new=start_remarketing,
        ),
        patch(
            "src.dashboard.handoff.get_temporal_client",
            new=AsyncMock(return_value=object()),
        ),
    ):
        res = client.post(
            "/api/dashboard/sessions/wa_6/return-to-bot",
            json={"target_route": "ventas"},
        )

    assert res.status_code == 200
    body = res.json()
    assert body["active_route"] == "ventas"
    assert body["tag"] == "RETOMA_VENTA"

    data = _read_metadata(vault, "wa_6")
    assert data["active_route"] == "ventas"
    assert data["tag"] == "RETOMA_VENTA"

    # Ventas NO arranca workflow proactivamente.
    start_remarketing.assert_not_awaited()


def test_return_to_bot_remarketing_starts_workflow_with_motivo(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_7", {"active_route": "humano", "tag": "HUMANO"})

    start_remarketing = AsyncMock()
    with (
        patch(
            "src.dashboard.handoff.start_remarketing_for_session",
            new=start_remarketing,
        ),
        patch(
            "src.dashboard.handoff.get_temporal_client",
            new=AsyncMock(return_value="fake_client"),
        ),
    ):
        res = client.post(
            "/api/dashboard/sessions/wa_7/return-to-bot",
            json={
                "target_route": "remarketing",
                "motivo": "cliente quedó indeciso, retomar suave en 2h",
            },
        )

    assert res.status_code == 200
    body = res.json()
    assert body["active_route"] == "remarketing"
    assert body["tag"] == "REMARKETING"

    # El workflow Remarketing se arrancó proactivamente con el motivo del humano.
    start_remarketing.assert_awaited_once()
    call_kwargs = start_remarketing.call_args.kwargs
    assert call_kwargs["session_id"] == "wa_7"
    assert "indeciso" in call_kwargs["motivo"]


def test_return_to_bot_remarketing_requires_motivo(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_8", {"active_route": "humano"})

    res = client.post(
        "/api/dashboard/sessions/wa_8/return-to-bot",
        json={"target_route": "remarketing"},
    )
    assert res.status_code == 400
    assert "motivo" in res.json()["detail"].lower()


def test_return_to_bot_rejects_when_not_in_humano_route(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_9", {"active_route": "ventas"})

    res = client.post(
        "/api/dashboard/sessions/wa_9/return-to-bot",
        json={"target_route": "ventas"},
    )
    assert res.status_code == 409


def test_return_to_bot_rejects_invalid_target(client_and_vault):
    client, vault = client_and_vault
    _write_metadata(vault, "wa_10", {"active_route": "humano"})

    res = client.post(
        "/api/dashboard/sessions/wa_10/return-to-bot",
        json={"target_route": "marte"},
    )
    assert res.status_code == 422  # pydantic Literal
