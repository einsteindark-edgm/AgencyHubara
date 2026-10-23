"""Mensajes interactivos (Flow y botón con enlace) DENTRO de la ventana 24h.

Con la ventana de servicio abierta, la invitación a calificar sale como mensaje
normal (más barato que una plantilla de marketing y sin aprobación de Meta) y
el agradecimiento lleva un botón con enlace a Google. Ambos tienen que:
  * llegar a Meta con el shape correcto (el Flow puede llevar foto arriba);
  * quedar en el historial del chat (el operador ve lo que recibió el cliente)
    y en el log de salientes del episodio (costo y `engaged`).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.platform.whatsapp.dtos import (
    InteractiveCTAUrlOutbound,
    InteractiveFlowOutbound,
    OutboundResult,
)
from src.platform.whatsapp.outbound import build_flow

SESSION = "wa_573001234567"


def test_flow_message_can_carry_a_photo_header():
    payload = build_flow(
        "573001234567",
        InteractiveFlowOutbound(
            flow_id="FLOW1",
            flow_token="review_ord_1",
            flow_cta="Calificar",
            flow_action_screen="RATING",
            body="¿Qué tal tu pedido?",
            header_media_id="MEDIA9",
        ),
        None,
    )
    assert payload["interactive"]["header"] == {"type": "image", "image": {"id": "MEDIA9"}}


@pytest.fixture
def isolated_vault(tmp_path, monkeypatch):
    monkeypatch.setattr("src.platform.whatsapp.activities.WORKSPACE_VAULT_DIR", tmp_path)
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE_ENV")
    folder = tmp_path / SESSION
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text(
        json.dumps({"phone_number_id": "PHONE_TEST", "episodes": [{"id": "ep1"}]}),
        encoding="utf-8",
    )
    return tmp_path


def _history(vault: Path) -> list[dict]:
    path = vault / SESSION / "sessions" / f"{SESSION}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _metadata(vault: Path) -> dict:
    return json.loads((vault / SESSION / "metadata.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_send_flow_to_session_sends_and_records(isolated_vault, monkeypatch):
    from src.platform.whatsapp.activities import send_flow_to_session

    mock_send = AsyncMock(return_value=OutboundResult(wa_message_id="wamid.F1", ok=True))
    monkeypatch.setattr("src.platform.whatsapp.activities.whatsapp_client.send_flow", mock_send)
    flow = InteractiveFlowOutbound(
        flow_id="FLOW1",
        flow_token="review_ord_1",
        flow_cta="Calificar",
        flow_action_screen="RATING",
        body="¿Qué tal tu pedido #31?",
    )

    result = await send_flow_to_session(
        SESSION, flow, history_text="¿Qué tal tu pedido #31? [Calificar]", image_url="/m/x.jpg"
    )

    assert result.ok
    phone, to, sent = mock_send.call_args.args
    assert (phone, to, sent) == ("PHONE_TEST", "573001234567", flow)
    [event] = _history(isolated_vault)
    assert event["content"] == "¿Qué tal tu pedido #31? [Calificar]"
    assert event["wamid"] == "wamid.F1"
    assert event["image_url"] == "/m/x.jpg"
    assert _metadata(isolated_vault)["last_outbound"]["kind"] == "interactive"


@pytest.mark.asyncio
async def test_failed_flow_send_leaves_no_history(isolated_vault, monkeypatch):
    from src.platform.whatsapp.activities import send_flow_to_session

    monkeypatch.setattr(
        "src.platform.whatsapp.activities.whatsapp_client.send_flow",
        AsyncMock(return_value=OutboundResult(wa_message_id=None, ok=False, error="131047")),
    )
    flow = InteractiveFlowOutbound(
        flow_id="FLOW1", flow_token="t", flow_cta="Calificar", flow_action_screen="RATING"
    )
    result = await send_flow_to_session(SESSION, flow, history_text="x")
    assert not result.ok
    assert not (isolated_vault / SESSION / "sessions").exists()


@pytest.mark.asyncio
async def test_send_cta_url_to_session_sends_and_records(isolated_vault, monkeypatch):
    from src.platform.whatsapp.activities import send_cta_url_to_session

    mock_send = AsyncMock(return_value=OutboundResult(wa_message_id="wamid.C1", ok=True))
    monkeypatch.setattr("src.platform.whatsapp.activities.whatsapp_client.send_cta_url", mock_send)
    cta = InteractiveCTAUrlOutbound(
        body="¡Gracias!", button_text="Opinar en Google", url="https://hubara.com.co/opinar/"
    )

    result = await send_cta_url_to_session(SESSION, cta)

    assert result.ok
    assert mock_send.call_args.args[2] == cta
    [event] = _history(isolated_vault)
    assert event["content"] == "¡Gracias!\n[Opinar en Google] https://hubara.com.co/opinar/"
    assert event["wamid"] == "wamid.C1"


# --- Premortem C5: lo que el bot no escribió queda marcado como aviso ---------


@pytest.mark.asyncio
async def test_flow_and_cta_history_events_are_notices_the_bot_did_not_write(isolated_vault, monkeypatch):
    """Sin `kind`/`sender` el evento parecía del bot y el turno siguiente
    creía que el LLM ya lo tenía en su historial (nunca supo que lo invitamos
    a calificar ni del cupón)."""
    from src.platform.whatsapp.activities import send_cta_url_to_session, send_flow_to_session

    monkeypatch.setattr(
        "src.platform.whatsapp.activities.whatsapp_client.send_flow",
        AsyncMock(return_value=OutboundResult(wa_message_id="wamid.F2", ok=True)),
    )
    monkeypatch.setattr(
        "src.platform.whatsapp.activities.whatsapp_client.send_cta_url",
        AsyncMock(return_value=OutboundResult(wa_message_id="wamid.C2", ok=True)),
    )
    flow = InteractiveFlowOutbound(flow_id="FLOW1", flow_token="review:o:1", flow_cta="Calificar",
                                   flow_action_screen="RATING", body="¿Qué tal tu pedido?")

    await send_flow_to_session(SESSION, flow, history_text="¿Qué tal tu pedido?", sender="reviews")
    await send_cta_url_to_session(SESSION, InteractiveCTAUrlOutbound(
        body="¡Gracias!", button_text="Opinar en Google", url="https://hubara.com.co/opinar/"))

    invitation, thanks = _history(isolated_vault)
    assert (invitation["kind"], invitation["sender"]) == ("notice", "reviews")
    assert (thanks["kind"], thanks["sender"]) == ("notice", "aviso")
