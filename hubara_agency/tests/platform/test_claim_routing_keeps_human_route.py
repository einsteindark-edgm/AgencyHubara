"""Tarjeta del ciclo #15: con la conversación en manos del equipo, ningún bot
le devuelve la ruta a sí mismo.

El remarketing que se apaga (inactividad, abstención, plantilla que no salió)
llama `claim_conversation_routing(session, "ventas")`. Si mientras tanto la
conversación pasó al equipo (`active_route=humano`), esa escritura la sacaba de
la ruta humano: el siguiente toque o mensaje del cliente volvía a despertar al
bot, que le hablaba al cliente encima de la persona que atendía.

Solo una persona devuelve la conversación al bot (el dashboard «Devolver al
bot», que no pasa por esta activity).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

SESSION = "wa_570000000015"


def _seed(vault: Path, data: dict) -> Path:
    path = vault / SESSION / "metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def activities(monkeypatch, tmp_path):
    import src.platform.temporal.activities as temporal_activities

    monkeypatch.setattr(temporal_activities, "WORKSPACE_VAULT_DIR", tmp_path)
    return temporal_activities


@pytest.mark.parametrize("bot_route", ["ventas", "remarketing"])
async def test_a_bot_cannot_take_the_route_back_from_the_team(activities, tmp_path, bot_route) -> None:
    path = _seed(tmp_path, {"active_route": "humano", "tag": "HUMANO", "status_history": []})

    await activities.claim_conversation_routing(SESSION, bot_route)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["active_route"] == "humano"
    assert data["tag"] == "HUMANO"


async def test_bots_still_hand_the_route_between_them(activities, tmp_path) -> None:
    path = _seed(tmp_path, {"active_route": "remarketing", "tag": "INTERESADO"})

    await activities.claim_conversation_routing(SESSION, "ventas")

    assert json.loads(path.read_text(encoding="utf-8"))["active_route"] == "ventas"


async def test_the_route_can_still_go_to_the_team(activities, tmp_path) -> None:
    path = _seed(tmp_path, {"active_route": "ventas"})

    await activities.claim_conversation_routing(SESSION, "humano")

    assert json.loads(path.read_text(encoding="utf-8"))["active_route"] == "humano"
