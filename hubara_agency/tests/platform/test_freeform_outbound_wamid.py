"""Cada burbuja de texto queda registrada con SU `wa_message_id`.

Incidente verificado en prod el 2026-09-18 (probe agregado del vault): 313/313
outbounds `kind="text"` tenían `wa_message_id=""` → cuando llegaba el webhook
`message_status` de Meta con el `pricing`, `IngestDeliveryStatus` no encontraba
a qué mensaje pertenecía → 3.012 webhooks en `_orphan_delivery_statuses.jsonl`
con `reason="not_found"` (2.961 traían precio). Resultado: el costo de TODO el
texto del bot quedaba "pendiente" para siempre y el `cost_summary` en $0 — solo
imágenes y plantillas (que sí guardaban el id) tenían precio.

Además un mensaje con N burbujas (`\\n\\n`) son N mensajes facturables para
Meta, cada uno con su id: una sola entrada los subcontaba.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.platform.state import FilesystemMetadataStore
from src.platform.whatsapp import activities as wa_activities
from src.platform.whatsapp.activities import send_message_to_session
from src.platform.whatsapp.composition import get_current_rate_card
from src.platform.whatsapp.dtos import OutboundResult
from src.plugins.chats.agent.sales.use_cases.ingest_delivery_status import (
    IngestDeliveryStatus,
)

SESSION = "wa_573001234567"


@pytest.fixture(autouse=True)
def _fast_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "PHONE_TEST")

    async def _no_sleep(_secs):
        return None

    monkeypatch.setattr(wa_activities.asyncio, "sleep", _no_sleep)


@pytest.fixture
def wamids(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    issued: list[str] = []

    async def fake_send(phone_number_id: str, to: str, text: str, reply_to_message_id=None):
        wamid = f"wamid.OUT{len(issued) + 1}"
        issued.append(wamid)
        return OutboundResult(wa_message_id=wamid, ok=True)

    monkeypatch.setattr(wa_activities.whatsapp_client, "send_text", fake_send)
    return issued


def _seed(vault: Path) -> None:
    d = vault / SESSION
    d.mkdir(parents=True, exist_ok=True)
    (d / "metadata.json").write_text(
        json.dumps(
            {
                "phone_number_id": "PHONE_TEST",
                "episodes": [{"episode_id": "ep_001", "closed_at_ms": None}],
            }
        ),
        encoding="utf-8",
    )


def _episode(vault: Path) -> dict:
    data = json.loads((vault / SESSION / "metadata.json").read_text(encoding="utf-8"))
    return data["episodes"][0]


@pytest.mark.asyncio
async def test_cada_burbuja_registra_su_wamid(_isolate_vault_dir: Path, wamids: list[str]):
    _seed(_isolate_vault_dir)
    await send_message_to_session(SESSION, "Hola 🌿\n\n¿Te ayudo a elegir?")

    outbounds = _episode(_isolate_vault_dir)["outbound_messages"]
    assert [o["wa_message_id"] for o in outbounds] == ["wamid.OUT1", "wamid.OUT2"]
    assert all(o["kind"] == "text" for o in outbounds)
    # Dos burbujas = dos mensajes facturables para Meta.
    assert _episode(_isolate_vault_dir)["cost_summary"]["messages_pending_count"] == 2


@pytest.mark.asyncio
async def test_el_webhook_de_meta_materializa_el_costo_del_texto(
    _isolate_vault_dir: Path, wamids: list[str], monkeypatch: pytest.MonkeyPatch
):
    """De punta a punta: envío → webhook `message_status` con pricing → costo."""
    monkeypatch.delenv("WHATSAPP_RATE_CARD_VERSION", raising=False)
    _seed(_isolate_vault_dir)
    await send_message_to_session(SESSION, "Hola 🌿\n\n¿Te ayudo a elegir?")

    use_case = IngestDeliveryStatus(
        metadata_store=FilesystemMetadataStore(_isolate_vault_dir),
        rate_card=get_current_rate_card(),
        event_bus=None,
        vault_dir=_isolate_vault_dir,
        retry_delays=(0.001,),
    )
    for wamid in wamids:
        await use_case.execute(
            wa_message_id=wamid,
            status="delivered",
            pricing={"billable": True, "type": "regular", "category": "marketing"},
        )

    episode = _episode(_isolate_vault_dir)
    assert [o["cost_usd_micros"] for o in episode["outbound_messages"]] == [12_500, 12_500]
    assert episode["cost_summary"]["total_usd_micros"] == 25_000
    assert episode["cost_summary"]["messages_pending_count"] == 0
    assert not (_isolate_vault_dir / "_orphan_delivery_statuses.jsonl").exists()
