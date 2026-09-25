"""Los estados de Meta de un mensaje de CAMPAÑA quedan en el touch del contacto.

Pedido del operador (2026-09-25): una campaña de marketing tiene que mostrar en
Ads entregados, leídos y gasto real, como una campaña de Meta. Antes, el
webhook de estados solo buscaba el mensaje en la conversación abierta del
contacto: a quien no tenía conversación abierta (casi toda la audiencia de
una campaña) el estado le llegaba "huérfano" y se perdía, y el estado mismo
(entregado / leído) no se guardaba en ningún lado.

Contrato:
- el parser lee la hora del estado y el código de error de Meta;
- el webhook se los pasa al use case;
- si el id del mensaje es de un touch de campaña, el use case anota en ese
  touch el estado (entregado / leído / fallido) y el precio, aunque el
  contacto no tenga conversación abierta; no es un huérfano;
- si además el mensaje está en la conversación abierta, esa conversación
  sigue materializando su costo como siempre.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.platform.state import FilesystemMetadataStore
from src.platform.whatsapp.cost import RateCard, RateCardEntry
from src.plugins.chats.agent.sales.parsers import parse_whatsapp_statuses
from src.plugins.chats.agent.sales.use_cases.ingest_delivery_status import (
    IngestDeliveryStatus,
)

_SESSION = "wa_573001234567"
_WAMID = "wamid.CAMP1"
_SENT_AT = 1_789_990_000_000
_PRICING = {"billable": True, "pricing_type": "regular", "category": "marketing"}


def _rate_card() -> RateCard:
    return RateCard(
        version="co_2026q3_v1",
        effective_from_ms=1_717_200_000_000,
        country="CO",
        currency="USD",
        rates={
            "marketing": RateCardEntry(usd_micros_per_message=12500),
            "utility": RateCardEntry(usd_micros_per_message=800),
            "service": RateCardEntry(usd_micros_per_message=0),
        },
    )


def _touch() -> dict[str, Any]:
    return {
        "campaign_id": "mkt-amor",
        "campaign_name": "Amor y amistad",
        "sent_at_ms": _SENT_AT,
        "wa_message_id": _WAMID,
    }


def _closed_episode() -> dict[str, Any]:
    return {"episode_id": "ep_001", "started_at_ms": _SENT_AT - 86_400_000,
            "closed_at_ms": _SENT_AT - 3_600_000, "outbound_messages": []}


async def _no_sleep(_s: float) -> None:
    return None


def _use_case(vault: Path) -> tuple[IngestDeliveryStatus, FilesystemMetadataStore]:
    store = FilesystemMetadataStore(vault)
    return (
        IngestDeliveryStatus(
            metadata_store=store,
            rate_card=_rate_card(),
            vault_dir=vault,
            sleeper=_no_sleep,
            retry_delays=(0.001,),
        ),
        store,
    )


def _orphans(vault: Path) -> list[dict[str, Any]]:
    path = vault / "_orphan_delivery_statuses.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# --- Parser -------------------------------------------------------------------


def test_the_parser_reads_the_status_time_and_metas_error_code() -> None:
    body = {"entry": [{"changes": [{"value": {"statuses": [
        {"id": "wamid.OK", "status": "delivered", "timestamp": "1789992000",
         "recipient_id": "573001234567", "pricing": _PRICING},
        {"id": "wamid.KO", "status": "failed", "timestamp": "1789992060",
         "errors": [{"code": 131049, "title": "This message was not delivered"}]},
        {"id": "wamid.SIN", "status": "sent"},
    ]}}]}]}

    ok, ko, bare = parse_whatsapp_statuses(body)

    assert ok.timestamp_ms == 1_789_992_000_000 and ok.error_code is None
    assert ko.timestamp_ms == 1_789_992_060_000 and ko.error_code == 131049
    assert bare.timestamp_ms is None and bare.error_code is None


# --- Webhook --------------------------------------------------------------------


def test_the_webhook_hands_the_status_time_and_error_to_the_use_case(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from src.main import app
    from src.platform import config
    from src.plugins.chats.api import sales as api

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class _Recorder:
        async def execute(self, *args: Any, **kwargs: Any) -> None:
            calls.append((args, kwargs))

    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "")
    monkeypatch.setattr(config, "HUBARA_ENV", "dev")
    monkeypatch.setattr(api, "build_ingest_delivery_status_use_case", lambda: _Recorder())
    status = {"id": "wamid.KO", "status": "failed", "timestamp": "1789992060",
              "recipient_id": "573001234567", "errors": [{"code": 131049}]}
    body = {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [
        {"field": "messages", "value": {"metadata": {"phone_number_id": "1"}, "statuses": [status]}}
    ]}]}

    r = TestClient(app).post("/api/webhook", json=body)

    assert r.status_code == 200
    [(args, kwargs)] = calls
    assert args[:2] == ("wamid.KO", "failed")
    assert kwargs == {"timestamp_ms": 1_789_992_060_000, "error_code": 131049}


# --- Use case -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_campaign_message_without_an_open_conversation_is_recorded_on_its_touch(
    tmp_path: Path,
) -> None:
    use_case, store = _use_case(tmp_path)
    store.write(_SESSION, {"episodes": [_closed_episode()], "campaign_touches": [_touch()]})

    await use_case.execute(_WAMID, "delivered", _PRICING, timestamp_ms=_SENT_AT + 2_000)
    await use_case.execute(_WAMID, "read", _PRICING, timestamp_ms=_SENT_AT + 60_000)

    delivery = store.read(_SESSION)["campaign_touches"][0]["delivery"]
    assert delivery["status"] == "read"
    assert delivery["delivered_at_ms"] == _SENT_AT + 2_000
    assert delivery["read_at_ms"] == _SENT_AT + 60_000
    # El precio real de la tarjeta vigente cuando salió (marketing CO).
    assert delivery["cost_usd_micros"] == 12500
    assert delivery["rate_card_version"] == "co_2026q3_v1"
    # No es un huérfano: quedó en la campaña.
    assert _orphans(tmp_path) == []


@pytest.mark.asyncio
async def test_a_campaign_message_that_failed_keeps_metas_code(tmp_path: Path) -> None:
    use_case, store = _use_case(tmp_path)
    store.write(_SESSION, {"campaign_touches": [_touch()]})

    await use_case.execute(_WAMID, "failed", None, timestamp_ms=_SENT_AT + 1_000, error_code=131049)

    delivery = store.read(_SESSION)["campaign_touches"][0]["delivery"]
    assert delivery["status"] == "failed"
    assert delivery["error_code"] == 131049
    assert _orphans(tmp_path) == []


@pytest.mark.asyncio
async def test_a_campaign_message_in_an_open_conversation_prices_both(tmp_path: Path) -> None:
    """El contacto tenía una conversación abierta: la plantilla quedó en su
    log de salientes (lo lee reengagement). La conversación materializa su
    costo como siempre y el touch de la campaña también lo anota."""
    use_case, store = _use_case(tmp_path)
    open_episode = {
        "episode_id": "ep_002",
        "started_at_ms": _SENT_AT - 3_600_000,
        "closed_at_ms": None,
        "outbound_messages": [{
            "sent_at_ms": _SENT_AT, "wa_message_id": _WAMID, "kind": "template",
            "template_name": "campaign_promo_marketing_v1", "pricing": None,
            "cost_usd_micros": None, "rate_card_version": None,
        }],
        "cost_summary": {"total_usd_micros": 0, "messages_count": 1,
                         "messages_billable_count": 0, "messages_free_count": 0,
                         "messages_pending_count": 1, "by_category": {}, "by_pricing_type": {}},
    }
    store.write(_SESSION, {"episodes": [open_episode], "campaign_touches": [_touch()]})

    await use_case.execute(_WAMID, "delivered", _PRICING, timestamp_ms=_SENT_AT + 2_000)

    metadata = store.read(_SESSION)
    assert metadata["episodes"][0]["outbound_messages"][0]["cost_usd_micros"] == 12500
    assert metadata["episodes"][0]["cost_summary"]["messages_pending_count"] == 0
    assert metadata["campaign_touches"][0]["delivery"]["cost_usd_micros"] == 12500


@pytest.mark.asyncio
async def test_a_message_that_is_not_from_a_campaign_is_still_an_orphan(tmp_path: Path) -> None:
    use_case, store = _use_case(tmp_path)
    store.write(_SESSION, {"campaign_touches": [_touch()]})

    await use_case.execute("wamid.OTRO", "delivered", _PRICING, timestamp_ms=_SENT_AT)

    assert [o["wa_message_id"] for o in _orphans(tmp_path)] == ["wamid.OTRO"]
    assert "delivery" not in store.read(_SESSION)["campaign_touches"][0]


@pytest.mark.asyncio
async def test_each_status_goes_to_the_campaign_of_its_message(tmp_path: Path) -> None:
    """Un cliente con dos campañas: cada aviso de Meta cae en la campaña de SU
    mensaje (el id del mensaje es único), nunca en la otra."""
    use_case, store = _use_case(tmp_path)
    store.write(_SESSION, {"campaign_touches": [
        {**_touch(), "campaign_id": "mkt-amor", "wa_message_id": "wamid.AMOR"},
        {**_touch(), "campaign_id": "mkt-halloween", "wa_message_id": "wamid.HALLOWEEN",
         "sent_at_ms": _SENT_AT + 3 * 86_400_000},
    ]})

    await use_case.execute("wamid.HALLOWEEN", "read", _PRICING, timestamp_ms=_SENT_AT + 3 * 86_400_000 + 60_000)
    await use_case.execute("wamid.AMOR", "failed", None, timestamp_ms=_SENT_AT + 1_000, error_code=131049)

    amor, halloween = store.read(_SESSION)["campaign_touches"]
    assert amor["delivery"]["status"] == "failed" and amor["delivery"]["error_code"] == 131049
    assert halloween["delivery"]["status"] == "read" and halloween["delivery"]["cost_usd_micros"] == 12500

