"""Citar un mensaje DEL BOT no debe salir "Mensaje no disponible".

Caso real (2026-09-17, captura del operador): la clienta escribió "Perfecto"
CITANDO una respuesta de texto del bot. La burbuja del dashboard mostró
"Mensaje anterior / Mensaje no disponible" porque:

* un evento `assistant` del JSONL es UN texto que salió como N burbujas
  (`send_message_to_session` fragmenta por `\\n\\n`), y ningún `wamid` de esas
  burbujas quedaba persistido → `_resolve_reply_quotes` no tenía con qué
  matchear el `context.id` del webhook;
* los templates y los componentes UI (catálogo, botones, picker, datos de
  pago) tampoco guardaban su `wamid`.

Contrato nuevo:

1. `send_message_to_session` indexa en `metadata.json[outbound_text_index]`
   el `wamid` → `{text, author}` de CADA burbuja entregada (acotado). Es el
   simétrico textual del `outbound_media_index` que ya existe para fotos.
2. El endpoint del dashboard resuelve la cita contra ese índice cuando no
   hay match en el JSONL.
3. El template send persiste su `wamid` en el evento del JSONL.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import src.platform.whatsapp.activities as activities
from src.platform.session_history import FilesystemMessageHistoryStore
from src.platform.whatsapp.dtos import OutboundResult


# ── Índice de burbujas salientes (choke point del texto free-form) ───────────


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(activities, "WORKSPACE_VAULT_DIR", tmp_path, raising=True)
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "phone-test")
    return tmp_path


def _seed_metadata(vault, session_id: str, data: dict) -> None:
    d = vault / session_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(json.dumps(data), encoding="utf-8")


def _metadata(vault, session_id: str) -> dict:
    return json.loads(
        (vault / session_id / "metadata.json").read_text(encoding="utf-8")
    )


async def _send(session_id: str, text: str, wamids: list[str], **kw) -> bool:
    results = [OutboundResult(wa_message_id=w, ok=True) for w in wamids]
    with (
        patch.object(
            activities.whatsapp_client,
            "send_text",
            new=AsyncMock(side_effect=results),
        ),
        patch.object(activities.asyncio, "sleep", new=AsyncMock()),
    ):
        return await activities.send_message_to_session(session_id, text, **kw)


@pytest.mark.asyncio
async def test_freeform_send_indexes_the_wamid_of_each_bubble(vault):
    """Cada chunk es una burbuja con su propio wamid — el cliente cita UNA."""
    _seed_metadata(vault, "wa_B1", {})

    assert await _send(
        "wa_B1",
        "Perfecto, tu pedido quedó registrado.\n\n¿Te ayudo con algo más?",
        ["wamid.chunk1", "wamid.chunk2"],
    )

    index = _metadata(vault, "wa_B1")["outbound_text_index"]
    assert index["wamid.chunk1"] == {
        "text": "Perfecto, tu pedido quedó registrado.",
        "author": "agent",
    }
    assert index["wamid.chunk2"]["text"] == "¿Te ayudo con algo más?"


@pytest.mark.asyncio
async def test_operator_text_is_indexed_as_human(vault):
    """El mismo choke point manda el texto del operador (dashboard handoff);
    la cita debe decir "Humano", no "Bot"."""
    _seed_metadata(vault, "wa_B2", {})

    assert await _send("wa_B2", "Ya te lo despachamos 🙌", ["wamid.h1"], author="human")

    assert _metadata(vault, "wa_B2")["outbound_text_index"]["wamid.h1"]["author"] == (
        "human"
    )


@pytest.mark.asyncio
async def test_rejected_bubble_is_not_indexed(vault):
    """Solo lo que Meta aceptó: indexar un rechazo haría que la cita muestre
    un texto que el cliente nunca recibió."""
    _seed_metadata(vault, "wa_B3", {})
    results = [
        OutboundResult(wa_message_id="wamid.ok", ok=True),
        OutboundResult(wa_message_id=None, ok=False, error="http_400: bad"),
    ]
    with (
        patch.object(
            activities.whatsapp_client, "send_text", new=AsyncMock(side_effect=results)
        ),
        patch.object(activities.asyncio, "sleep", new=AsyncMock()),
    ):
        assert await activities.send_message_to_session("wa_B3", "uno\n\ndos") is False

    assert list(_metadata(vault, "wa_B3")["outbound_text_index"]) == ["wamid.ok"]


@pytest.mark.asyncio
async def test_text_index_is_capped(vault):
    """metadata.json no puede crecer sin límite en un chat largo: se evictan
    las burbujas más viejas (las citas son sobre lo reciente)."""
    seed = {
        "outbound_text_index": {
            f"wamid.old{i}": {"text": f"vieja {i}", "author": "agent"}
            for i in range(activities.OUTBOUND_TEXT_INDEX_MAX)
        }
    }
    _seed_metadata(vault, "wa_B4", seed)

    assert await _send("wa_B4", "la nueva", ["wamid.new"])

    index = _metadata(vault, "wa_B4")["outbound_text_index"]
    assert len(index) == activities.OUTBOUND_TEXT_INDEX_MAX
    assert "wamid.new" in index
    assert "wamid.old0" not in index  # la más vieja se fue


# ── Resolución en el endpoint del dashboard ──────────────────────────────────


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "test_phone")
    with patch("src.plugins.chats.api.dashboard.WORKSPACE_VAULT_DIR", tmp_path):
        from src.main import app

        yield TestClient(app), tmp_path


def test_dashboard_resolves_quote_to_a_bot_bubble(dashboard):
    client, vault = dashboard
    _seed_metadata(
        vault,
        "wa_Q1",
        {
            "outbound_text_index": {
                "wamid.bot.bubble": {
                    "text": "Tu pedido quedó registrado ✨",
                    "author": "agent",
                }
            }
        },
    )
    store = FilesystemMessageHistoryStore(vault)
    store.append_assistant_event(
        "wa_Q1", "Tu pedido quedó registrado ✨\n\n¿Algo más?"
    )
    store.append_user_event(
        "wa_Q1", "Perfecto", wamid="wamid.in", reply_to={"id": "wamid.bot.bubble"}
    )

    msgs = client.get("/api/dashboard/sessions/wa_Q1").json()["messages"]

    assert msgs[1]["reply_to"] == {
        "id": "wamid.bot.bubble",
        "author": "agent",
        "text": "Tu pedido quedó registrado ✨",
    }


def test_dashboard_resolves_quote_to_an_operator_bubble(dashboard):
    client, vault = dashboard
    _seed_metadata(
        vault,
        "wa_Q2",
        {
            "outbound_text_index": {
                "wamid.op": {"text": "Sale hoy por Servientrega", "author": "human"}
            }
        },
    )
    store = FilesystemMessageHistoryStore(vault)
    store.append_user_event("wa_Q2", "gracias!", reply_to={"id": "wamid.op"})

    msgs = client.get("/api/dashboard/sessions/wa_Q2").json()["messages"]

    assert msgs[0]["reply_to"]["author"] == "human"
    assert msgs[0]["reply_to"]["text"] == "Sale hoy por Servientrega"


def test_jsonl_match_wins_over_the_index(dashboard):
    """El JSONL es la fuente de verdad; el índice es el fallback."""
    client, vault = dashboard
    _seed_metadata(
        vault,
        "wa_Q3",
        {"outbound_text_index": {"wamid.x": {"text": "del índice", "author": "agent"}}},
    )
    store = FilesystemMessageHistoryStore(vault)
    store.append_human_event("wa_Q3", "del JSONL", wamid="wamid.x")
    store.append_user_event("wa_Q3", "esa", reply_to={"id": "wamid.x"})

    msgs = client.get("/api/dashboard/sessions/wa_Q3").json()["messages"]

    assert msgs[1]["reply_to"]["text"] == "del JSONL"


def test_unknown_quote_still_degrades_to_just_the_id(dashboard):
    """Sin match en ninguna fuente (burbuja evictada, chat viejo) la cita
    queda con el id — el frontend muestra el fallback, no revienta."""
    client, vault = dashboard
    _seed_metadata(vault, "wa_Q4", {})
    store = FilesystemMessageHistoryStore(vault)
    store.append_user_event("wa_Q4", "esa", reply_to={"id": "wamid.gone"})

    msgs = client.get("/api/dashboard/sessions/wa_Q4").json()["messages"]

    assert msgs[0]["reply_to"] == {"id": "wamid.gone"}


# ── Templates ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_template_event_persists_its_wamid(tmp_path, monkeypatch):
    """El cliente responde a la plantilla de cotización/ETA citándola: sin el
    wamid en el evento, esa cita sale "no disponible"."""
    monkeypatch.setattr(
        "src.platform.whatsapp.activities.WORKSPACE_VAULT_DIR", tmp_path
    )
    session_id = "wa_T1"
    _seed_metadata(tmp_path, session_id, {"phone_number_id": "PHONE_TEST"})
    monkeypatch.setattr(
        "src.platform.whatsapp.activities.whatsapp_client.send_template",
        AsyncMock(return_value=OutboundResult(wa_message_id="wamid.tpl", ok=True)),
    )

    await activities.send_template_to_session(
        session_id, "quote_ready_utility_v2", {"product_or_quote_label": "vela"}
    )

    line = (
        (tmp_path / session_id / "sessions" / f"{session_id}.jsonl")
        .read_text(encoding="utf-8")
        .strip()
    )
    assert json.loads(line)["wamid"] == "wamid.tpl"
