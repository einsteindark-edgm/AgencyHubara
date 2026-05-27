"""Test E2E pseudo-integration del flow completo outbound → cost (HU-WA24H-001 F1.12).

Simula el flujo end-to-end del refinement §4.7:

    send_whatsapp_template_activity persiste OutboundLogEntry pending al
    metadata → retorna wa_message_id

    Meta envía webhook status `delivered` con pricing object

    parse_whatsapp_statuses extrae el status update

    IngestDeliveryStatus.execute encuentra el entry pending, computa
    cost via rate_card, materializa el cost en el entry, y actualiza
    cost_summary del episodio

Verificamos:
  * El entry pasó de pending (cost_cents_usd=None) a materializado
    (cost_cents_usd=125 para marketing CO 2026Q2, o 8 para utility).
  * cost_summary del episodio refleja el nuevo cost: total_cents_usd,
    messages_billable_count, by_category, by_pricing_type.
  * El analytics event `delivery_status` se emitió con todos los campos.

F1.7 (la activity real `send_whatsapp_template_activity`) NO está
implementada todavía — este test mocka su comportamiento esperado:
escribir el OutboundLogEntry pending al metadata atómicamente, antes
de retornar el wa_message_id (key insight del refinement §4.7 para
evitar la race del webhook).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from src.platform.analytics.bus import EventBus
from src.platform.analytics.events import AnalyticsEvent
from src.platform.state import FilesystemMetadataStore
from src.platform.whatsapp.cost import (
    OutboundLogEntry,
    RateCard,
    RateCardEntry,
    add_outbound_to_summary,
    empty_episode_cost_summary,
)
from src.plugins.chats.agent.sales.parsers import parse_whatsapp_statuses
from src.plugins.chats.agent.sales.use_cases.ingest_delivery_status import (
    IngestDeliveryStatus,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def co_rate_card() -> RateCard:
    return RateCard(
        version="co_2026q2_v1",
        effective_from_ms=1_717_200_000_000,
        country="CO",
        currency="USD",
        rates={
            "marketing": RateCardEntry(cents_per_message=125),
            "utility": RateCardEntry(cents_per_message=8),
            "authentication": RateCardEntry(cents_per_message=8),
            "service": RateCardEntry(cents_per_message=0),
        },
    )


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    return vault


class _RecordingSink:
    name = "recording"

    def __init__(self) -> None:
        self.events: list[AnalyticsEvent] = []

    async def write(self, event: AnalyticsEvent) -> None:
        self.events.append(event)


async def _no_sleep(_s: float) -> None:
    return None


# =============================================================================
# Fake send activity (lo que F1.7 hará en prod)
# =============================================================================


@dataclass(frozen=True)
class _FakeOutboundResult:
    """Mimic de OutboundResult que F1.7 retornará."""

    wa_message_id: str
    ok: bool = True
    error: str | None = None


def _ensure_active_episode(metadata: dict[str, Any], *, now_ms: int) -> dict[str, Any]:
    """Garantiza UN episodio activo (espejo simplificado de
    ensure_active_episode). Suficiente para este E2E."""
    episodes: list[dict[str, Any]] = metadata.setdefault("episodes", [])
    if episodes and episodes[-1].get("closed_at_ms") is None:
        return episodes[-1]
    new_ep = {
        "episode_id": f"ep_{len(episodes) + 1:03d}",
        "started_at_ms": now_ms,
        "started_inbound_message_id": None,
        "closed_at_ms": None,
        "closing_tag": None,
        "closing_motivo": None,
        "order_id": None,
        "referral_snapshot": None,
        "msgs_count_at_start": 0,
        "msgs_count_at_close": None,
        "outbound_messages": [],
        "cost_summary": _summary_to_dict_e2e(empty_episode_cost_summary()),
    }
    episodes.append(new_ep)
    return new_ep


def _summary_to_dict_e2e(summary):
    """Serializa EpisodeCostSummary para test (copia local del helper interno)."""
    return {
        "total_cents_usd": summary.total_cents_usd,
        "messages_count": summary.messages_count,
        "messages_billable_count": summary.messages_billable_count,
        "messages_free_count": summary.messages_free_count,
        "messages_pending_count": summary.messages_pending_count,
        "by_category": {
            k: {"count": v.count, "cents_usd": v.cents_usd}
            for k, v in summary.by_category.items()
        },
        "by_pricing_type": {
            k: {"count": v.count, "cents_usd": v.cents_usd}
            for k, v in summary.by_pricing_type.items()
        },
    }


async def fake_send_whatsapp_template_activity(
    *,
    metadata_store: FilesystemMetadataStore,
    session_id: str,
    wa_message_id: str,
    template_name: str,
    sent_at_ms: int,
) -> _FakeOutboundResult:
    """Mock de F1.7 — la activity real `send_whatsapp_template_activity`.

    Persiste UN `OutboundLogEntry` pending en el episodio activo de la
    sesión ANTES de retornar el wa_message_id. Atómico — clave para que
    el webhook delivery status que llegue después encuentre la entry.

    Esto es lo que el activity real hará en prod (refinement §4.7 +
    F1.7 spec del Sprint 1).
    """
    metadata = metadata_store.read(session_id)
    if not metadata:
        metadata = {"phone_number_id": "PID"}
    episode = _ensure_active_episode(metadata, now_ms=sent_at_ms)

    pending_entry = OutboundLogEntry(
        sent_at_ms=sent_at_ms,
        wa_message_id=wa_message_id,
        kind="template",
        template_name=template_name,
        pricing=None,
        cost_cents_usd=None,
        rate_card_version=None,
    )
    outbound_messages = list(episode.get("outbound_messages") or [])
    outbound_messages.append(
        {
            "sent_at_ms": pending_entry.sent_at_ms,
            "wa_message_id": pending_entry.wa_message_id,
            "kind": pending_entry.kind,
            "template_name": pending_entry.template_name,
            "pricing": None,
            "cost_cents_usd": None,
            "rate_card_version": None,
        }
    )
    episode["outbound_messages"] = outbound_messages

    # Actualizar summary con el pending (pending_count += 1)
    from src.platform.whatsapp.cost import EpisodeCostSummary

    current_summary_raw = episode.get("cost_summary") or {}
    current_summary = EpisodeCostSummary(
        total_cents_usd=current_summary_raw.get("total_cents_usd", 0),
        messages_count=current_summary_raw.get("messages_count", 0),
        messages_billable_count=current_summary_raw.get(
            "messages_billable_count", 0
        ),
        messages_free_count=current_summary_raw.get("messages_free_count", 0),
        messages_pending_count=current_summary_raw.get("messages_pending_count", 0),
        by_category={},
        by_pricing_type={},
    )
    new_summary = add_outbound_to_summary(current_summary, pending_entry)
    episode["cost_summary"] = _summary_to_dict_e2e(new_summary)

    metadata_store.write(session_id, metadata)
    return _FakeOutboundResult(wa_message_id=wa_message_id)


# =============================================================================
# E2E test — send template → webhook delivered → cost materialized
# =============================================================================


def _build_webhook_status_body(
    *,
    phone_number_id: str,
    wa_message_id: str,
    status: str,
    pricing: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build el shape Meta del webhook `message_status`."""
    status_obj: dict[str, Any] = {
        "id": wa_message_id,
        "status": status,
        "timestamp": "1716700100",
        "recipient_id": "+5730099999",
    }
    if pricing is not None:
        status_obj["pricing"] = pricing
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1234567890",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "+57XXX",
                                "phone_number_id": phone_number_id,
                            },
                            "statuses": [status_obj],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_send_template_then_delivered_webhook_materializes_cost(
    vault_dir: Path, co_rate_card: RateCard
) -> None:
    """Flujo completo: send template → webhook delivered → cost materializado."""
    sink = _RecordingSink()
    bus = EventBus(sinks=[sink])
    store = FilesystemMetadataStore(vault_dir)
    session_id = "wa_+5730099999"
    wa_msg = "wamid.E2E.TEMPLATE.1"
    sent_at_ms = 1_716_700_100_000

    # 1) F1.7 (mocked) — activity persiste OutboundLogEntry pending
    result = await fake_send_whatsapp_template_activity(
        metadata_store=store,
        session_id=session_id,
        wa_message_id=wa_msg,
        template_name="quote_ready_utility_v1",
        sent_at_ms=sent_at_ms,
    )
    assert result.ok
    assert result.wa_message_id == wa_msg

    # Verificar el estado pending intermedio (sanity check)
    metadata_after_send = store.read(session_id)
    pending_entry = metadata_after_send["episodes"][0]["outbound_messages"][0]
    assert pending_entry["wa_message_id"] == wa_msg
    assert pending_entry["cost_cents_usd"] is None
    assert pending_entry["pricing"] is None
    pending_summary = metadata_after_send["episodes"][0]["cost_summary"]
    assert pending_summary["messages_pending_count"] == 1
    assert pending_summary["total_cents_usd"] == 0

    # 2) Meta envía webhook status `delivered` con pricing
    webhook_body = _build_webhook_status_body(
        phone_number_id="PID",
        wa_message_id=wa_msg,
        status="delivered",
        pricing={
            "billable": True,
            "pricing_type": "regular",
            "category": "utility",
        },
    )

    # 3) Parse del body → status updates
    status_updates = parse_whatsapp_statuses(webhook_body)
    assert len(status_updates) == 1
    update = status_updates[0]
    assert update.wa_message_id == wa_msg
    assert update.status == "delivered"
    assert update.pricing == {
        "billable": True,
        "pricing_type": "regular",
        "category": "utility",
    }

    # 4) IngestDeliveryStatus.execute → cost materializado
    use_case = IngestDeliveryStatus(
        metadata_store=store,
        rate_card=co_rate_card,
        event_bus=bus,
        vault_dir=vault_dir,
        sleeper=_no_sleep,
        retry_delays=(0.001,),
    )
    await use_case.execute(
        wa_message_id=update.wa_message_id,
        status=update.status,
        pricing=update.pricing,
    )

    # 5) Asserts finales — entry materializada
    final_metadata = store.read(session_id)
    final_entry = final_metadata["episodes"][0]["outbound_messages"][0]
    assert final_entry["wa_message_id"] == wa_msg
    assert final_entry["cost_cents_usd"] == 8  # utility CO 2026Q2 = 8 cents
    assert final_entry["rate_card_version"] == "co_2026q2_v1"
    assert final_entry["pricing"] == {
        "billable": True,
        "pricing_type": "regular",
        "category": "utility",
    }

    # Summary actualizado
    final_summary = final_metadata["episodes"][0]["cost_summary"]
    assert final_summary["total_cents_usd"] == 8
    assert final_summary["messages_count"] == 1
    assert final_summary["messages_billable_count"] == 1
    assert final_summary["messages_pending_count"] == 0
    assert final_summary["messages_free_count"] == 0
    assert final_summary["by_category"]["utility"] == {"count": 1, "cents_usd": 8}
    assert final_summary["by_pricing_type"]["regular"] == {
        "count": 1,
        "cents_usd": 8,
    }

    # Invariante cross-cutting
    assert final_summary["messages_count"] == (
        final_summary["messages_billable_count"]
        + final_summary["messages_free_count"]
        + final_summary["messages_pending_count"]
    )

    # Analytics event emitido
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.kind == "delivery_status"
    assert event.correlation["session_id"] == session_id
    assert event.correlation["wa_message_id"] == wa_msg
    assert event.payload["status"] == "delivered"
    assert event.payload["category"] == "utility"
    assert event.payload["pricing_type"] == "regular"
    assert event.payload["billable"] is True
    assert event.payload["cost_cents_usd"] == 8


@pytest.mark.asyncio
async def test_send_template_then_free_window_webhook_zero_cost(
    vault_dir: Path, co_rate_card: RateCard
) -> None:
    """E2E con free_customer_service: dentro de ventana 24h, costo 0."""
    sink = _RecordingSink()
    bus = EventBus(sinks=[sink])
    store = FilesystemMetadataStore(vault_dir)
    session_id = "wa_+5730088888"
    wa_msg = "wamid.E2E.FREE.1"

    await fake_send_whatsapp_template_activity(
        metadata_store=store,
        session_id=session_id,
        wa_message_id=wa_msg,
        template_name="order_status_utility_v1",
        sent_at_ms=1_716_700_100_000,
    )

    webhook_body = _build_webhook_status_body(
        phone_number_id="PID",
        wa_message_id=wa_msg,
        status="delivered",
        pricing={
            "billable": True,
            "pricing_type": "free_customer_service",
            "category": "service",
        },
    )
    status_updates = parse_whatsapp_statuses(webhook_body)

    use_case = IngestDeliveryStatus(
        metadata_store=store,
        rate_card=co_rate_card,
        event_bus=bus,
        vault_dir=vault_dir,
        sleeper=_no_sleep,
    )
    for update in status_updates:
        await use_case.execute(
            wa_message_id=update.wa_message_id,
            status=update.status,
            pricing=update.pricing,
        )

    final_metadata = store.read(session_id)
    final_entry = final_metadata["episodes"][0]["outbound_messages"][0]
    assert final_entry["cost_cents_usd"] == 0

    final_summary = final_metadata["episodes"][0]["cost_summary"]
    assert final_summary["total_cents_usd"] == 0
    assert final_summary["messages_free_count"] == 1
    assert final_summary["messages_billable_count"] == 0
    assert final_summary["messages_pending_count"] == 0
    assert final_summary["by_pricing_type"]["free_customer_service"] == {
        "count": 1,
        "cents_usd": 0,
    }
