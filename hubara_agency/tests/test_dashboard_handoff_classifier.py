"""Tests del clasificador `ui_type` del endpoint `get_session_history`.

Verifica que un evento del humano (escrito por `append_human_event`) se
proyecta como `ui_type: "human_message"` para que el frontend lo renderice
diferenciado del agent_message.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.platform.session_history import FilesystemMessageHistoryStore


@pytest.fixture
def client_with_temp_vault(tmp_path, monkeypatch):
    """Apunta WORKSPACE_VAULT_DIR a un dir temporal y arma TestClient."""
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "test_phone")
    # El dashboard router usa WORKSPACE_VAULT_DIR via import-time. Hacemos
    # patch directo del símbolo en el módulo del dashboard.
    with patch("src.dashboard.api.WORKSPACE_VAULT_DIR", tmp_path):
        from src.main import app
        yield TestClient(app), tmp_path


def test_human_event_classified_as_human_message(client_with_temp_vault):
    client, vault = client_with_temp_vault
    store = FilesystemMessageHistoryStore(vault)
    # Mezcla representativa: user → assistant del bot → human → user de nuevo.
    store.append_user_event("wa_X", "necesito hablar con un humano")
    store.append_assistant_event("wa_X", "te conecto con un colega 🤍")
    store.append_human_event("wa_X", "Hola, te ayudo personalmente")
    store.append_user_event("wa_X", "ok perfecto")

    res = client.get("/api/dashboard/sessions/wa_X")
    assert res.status_code == 200
    body = res.json()
    msgs = body["messages"]
    assert len(msgs) == 4
    assert msgs[0]["ui_type"] == "user_message"
    assert msgs[1]["ui_type"] == "agent_message"
    assert msgs[2]["ui_type"] == "human_message"  # ← clave
    assert msgs[2]["sender"] == "human"
    assert msgs[2]["content"] == "Hola, te ayudo personalmente"
    assert msgs[3]["ui_type"] == "user_message"


def test_assistant_message_without_sender_stays_agent_message(client_with_temp_vault):
    """Mensajes assistant sin `sender=human` siguen como agent_message
    (no regresamos por ahí — esto es el guardrail anti-regresión)."""
    client, vault = client_with_temp_vault
    store = FilesystemMessageHistoryStore(vault)
    store.append_assistant_event("wa_Y", "respuesta del bot, no del humano")

    res = client.get("/api/dashboard/sessions/wa_Y")
    body = res.json()
    msgs = body["messages"]
    assert len(msgs) == 1
    assert msgs[0]["ui_type"] == "agent_message"


def test_assistant_with_tool_calls_still_classified_as_tool_call_even_if_unknown_extra(
    client_with_temp_vault,
):
    """assistant + tool_calls SIN sender=human → agent_tool_call.

    Verifica que la rama nueva (sender=human) no roba prioridad a la rama
    de tool_calls cuando el sender no está marcado.
    """
    client, vault = client_with_temp_vault
    store = FilesystemMessageHistoryStore(vault)
    store.append_assistant_event(
        "wa_Z",
        "",
        tool_calls=[{"id": "tc_1", "name": "search_products"}],
    )

    res = client.get("/api/dashboard/sessions/wa_Z")
    body = res.json()
    msgs = body["messages"]
    assert msgs[0]["ui_type"] == "agent_tool_call"
