"""El detalle de la sesión EMITE el evento estructurado de cada mensaje.

Gotcha #1 del repo: que el parser sepa proyectar el marker no basta — el
endpoint que alimenta el panel del chat tiene que mandarlo. Sin esto el
frontend seguiría pintando texto plano con tests verdes.

La correlación del botón tocado también se resuelve acá (server-side): el
frontend no adivina a qué tanda de botones pertenece un tap.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.plugins.chats.api.dashboard as dash_mod

SESSION = "wa_573229041190"


@pytest.fixture
def client_and_vault(tmp_path):
    app = FastAPI()
    app.include_router(dash_mod.router, prefix="/api/dashboard")
    with patch.object(dash_mod, "WORKSPACE_VAULT_DIR", tmp_path):
        yield TestClient(app), tmp_path


def _seed_history(vault, *events: dict) -> None:
    path = vault / SESSION / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{SESSION}.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events),
        encoding="utf-8",
    )


def _messages(client) -> list[dict]:
    resp = client.get(f"/api/dashboard/sessions/{SESSION}")
    assert resp.status_code == 200
    return resp.json()["messages"]


def test_photo_message_carries_caption_and_vision_apart(client_and_vault):
    client, vault = client_and_vault
    _seed_history(
        vault,
        {
            "role": "user",
            "content": '[el cliente envió una foto: Vela con figura de pareja.]'
            ' con el texto: "Precio?"',
            "image_url": "/api/dashboard/media/wa_x/1.jpg",
            "timestamp": "2026-09-17T18:18:00Z",
        },
    )

    event = _messages(client)[0]["event"]

    assert event["kind"] == "customer_photo"
    assert event["caption"] == "Precio?"
    assert event["vision"] == "Vela con figura de pareja."


def test_buttons_message_carries_body_titles_and_the_touched_one(client_and_vault):
    client, vault = client_and_vault
    _seed_history(
        vault,
        {
            "role": "assistant",
            "kind": "ui_component",
            "component_kind": "quick_replies",
            "content": "🔘 El bot envió botones: Ver catálogo · Asesoría"
            " — con el mensaje: «Buenas tardes.»",
            "timestamp": "2026-09-17T18:17:00Z",
        },
        {
            "role": "user",
            "content": "[el cliente tocó el botón: Ver catálogo]",
            "timestamp": "2026-09-17T18:18:00Z",
        },
    )

    messages = _messages(client)

    assert messages[0]["event"] == {
        "kind": "bot_buttons",
        "body": "Buenas tardes.",
        "buttons": [
            {"title": "Ver catálogo", "touched": True},
            {"title": "Asesoría"},
        ],
    }
    assert messages[1]["event"] == {"kind": "button_tap", "title": "Ver catálogo"}


def test_reaction_message_carries_the_emoji(client_and_vault):
    client, vault = client_and_vault
    _seed_history(
        vault,
        {
            "role": "user",
            "content": "[el cliente reaccionó con ❤️]",
            "timestamp": "2026-09-17T18:19:00Z",
        },
    )

    assert _messages(client)[0]["event"] == {
        "kind": "reaction",
        "emoji": "❤️",
        "author": "user",
    }


def test_plain_messages_carry_no_event(client_and_vault):
    client, vault = client_and_vault
    _seed_history(
        vault,
        {"role": "user", "content": "Hola", "timestamp": "2026-09-17T18:20:00Z"},
        {
            "role": "assistant",
            "content": "¡Hola! Bienvenido.",
            "timestamp": "2026-09-17T18:20:05Z",
        },
    )

    assert all("event" not in m for m in _messages(client))
