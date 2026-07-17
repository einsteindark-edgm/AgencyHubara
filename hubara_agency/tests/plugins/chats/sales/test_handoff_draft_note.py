"""El turno de handoff Remarketing→Sales lleva el draft del pedido.

Incidente 2026-07-17 (run 019f6db3): el cliente respondió al remarketing
"Vamos con esas dos" (1× Leo café + 1× Libra sándalo, anotadas en el draft
de la tarde). El handoff arrancó el workflow Sales con el prompt SIN el
bloque `[DATOS DEL PEDIDO YA CONFIRMADOS]` — esa inyección solo existía en
el path del webhook (`ingest_inbound_message`), no en el path de handoff.
El LLM, ciego, sobrescribió las notas explorando y le re-preguntó todo.

`read_order_draft_note_activity` cierra el hueco: el workflow la llama al
consumir un pending_handoff y adjunta la note como plugin_context del turno.
"""
from __future__ import annotations

import json

import pytest

from src.plugins.chats.agent.sales.activities.bootstrap_session import (
    read_order_draft_note_activity,
)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.plugins.chats.agent.sales.activities.bootstrap_session.WORKSPACE_VAULT_DIR",
        tmp_path,
    )
    return tmp_path


def _write_metadata(vault, session_id: str, data: dict) -> None:
    session_dir = vault / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "metadata.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


_DRAFT_METADATA = {
    "episodes": [
        {
            "episode_id": "ep_003",
            "started_at_ms": 1784000000000,
            "order_draft": {
                "slots": {
                    "producto": "Duo Zodiacal",
                    "aroma": "Café, Sándalo",
                    "cantidad": "2",
                    "notas": (
                        "1× Duo Zodiacal - Signo Leo, aroma Café + "
                        "1× Duo Zodiacal - Signo Libra, aroma Sándalo"
                    ),
                }
            },
        }
    ]
}


@pytest.mark.asyncio
async def test_returns_note_with_draft_slots(vault):
    _write_metadata(vault, "wa_1", _DRAFT_METADATA)
    note = await read_order_draft_note_activity("wa_1")
    assert note is not None
    assert "DATOS DEL PEDIDO YA CONFIRMADOS" in note
    assert "Signo Leo" in note
    assert "Signo Libra" in note
    assert "Café, Sándalo" in note


@pytest.mark.asyncio
async def test_none_without_metadata(vault):
    assert await read_order_draft_note_activity("wa_nueva") is None


@pytest.mark.asyncio
async def test_none_when_episode_closed(vault):
    data = json.loads(json.dumps(_DRAFT_METADATA))
    data["episodes"][0]["closed_at_ms"] = 1784000001000
    _write_metadata(vault, "wa_2", data)
    assert await read_order_draft_note_activity("wa_2") is None


@pytest.mark.asyncio
async def test_none_when_order_already_registered(vault):
    data = json.loads(json.dumps(_DRAFT_METADATA))
    data["episodes"][0]["order_id"] = "order_x"
    _write_metadata(vault, "wa_3", data)
    assert await read_order_draft_note_activity("wa_3") is None
