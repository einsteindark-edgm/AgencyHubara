"""La bandeja de chats (`/api/dashboard/sessions`) y el panel de una
conversación (`/api/dashboard/sessions/<id>`) frente a un `metadata.json`
dañado (PR #393, decisión del operador del 2026-10-06).

Los dos leían el archivo directo: con un JSON roto mostraban una conversación
humana como `ventas` / `NO_ETIQUETADO` (el operador no la veía en su cola), y
con bytes que no son UTF-8 o sin permiso de lectura la lista ENTERA fallaba (y
la conversación no abría). Ahora leen con el store: la última copia buena, como
el resto del sistema.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.plugins.chats.api import dashboard

SID = "wa_573001234567"
_HUMAN = {"active_route": "humano", "tag": "HUMANO", "motivo": "Lo atiende Ana", "phone_number_id": "pnid-1"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, Path]]:
    app = FastAPI()
    app.include_router(dashboard.router, prefix="/api/dashboard")
    monkeypatch.setattr(dashboard, "_resolve_ad_names", lambda ids: {})
    with patch("src.plugins.chats.api.dashboard.WORKSPACE_VAULT_DIR", tmp_path):
        yield TestClient(app, raise_server_exceptions=False), tmp_path


def _broken_json(path: Path) -> None:
    path.write_text('{"active_route": "humano", "tag": "HUM', encoding="utf-8")


def _not_utf8(path: Path) -> None:
    path.write_bytes(b'{"active_route": "humano", "x": "\xff\xfe"}')


def _no_permission(path: Path) -> None:
    path.write_text(json.dumps(_HUMAN), encoding="utf-8")
    os.chmod(path, 0)


_DAMAGE = [
    pytest.param(_broken_json, id="json-roto"),
    pytest.param(_not_utf8, id="utf8-invalido"),
    pytest.param(
        _no_permission,
        id="permisos-000",
        marks=pytest.mark.skipif(os.geteuid() == 0, reason="root lee archivos 000"),
    ),
]


def _damaged_human_conversation(vault: Path, damage) -> None:
    path = vault / SID / "metadata.json"
    path.parent.mkdir(parents=True)
    path.with_name("metadata.json.prev").write_text(json.dumps(_HUMAN), encoding="utf-8")
    damage(path)


@pytest.mark.parametrize("damage", _DAMAGE)
def test_the_inbox_shows_a_damaged_conversation_from_its_last_good_copy(client, damage) -> None:
    c, vault = client
    _damaged_human_conversation(vault, damage)

    response = c.get("/api/dashboard/sessions")

    assert response.status_code == 200, "un archivo dañado tumbó la bandeja entera"
    row = {s["session_id"]: s for s in response.json()["sessions"]}[SID]
    assert (row["active_agent_route"], row["tag"]) == ("humano", "HUMANO"), "la conversación humana salió como del bot"


@pytest.mark.parametrize("damage", _DAMAGE)
def test_the_conversation_view_shows_a_damaged_conversation_from_its_last_good_copy(client, damage) -> None:
    """El panel de la conversación (`/api/dashboard/sessions/<id>`) leía el
    archivo directo, igual que la bandeja: con un JSON roto la mostraba del
    bot y con UTF-8 inválido o sin permiso no abría."""
    c, vault = client
    _damaged_human_conversation(vault, damage)

    response = c.get(f"/api/dashboard/sessions/{SID}")

    assert response.status_code == 200, "un archivo dañado no deja abrir la conversación"
    body = response.json()
    assert (body["active_agent_route"], body["tag"], body["motivo"]) == ("humano", "HUMANO", "Lo atiende Ana")
