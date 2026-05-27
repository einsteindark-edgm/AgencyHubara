"""Tests del use case `IngestDeliveryStatus` (HU-WA24H-001 F1.10).

Cubre los seis escenarios del refinement §4.7:

1. Webhook regular marketing → cost computed correcto + summary actualizado.
2. Webhook free_customer_service → cost=0 + free_count++.
3. Race: webhook antes de que outbound exista → retry 3 veces → dead-letter.
4. Duplicate webhook (mismo wa_message_id) → idempotente, invariantes intactos.
5. Webhook a episodio cerrado → dead-letter, NO muta summary.
6. Webhook sin pricing object → persiste el status pero NO toca cost.

Convención: el use case es puro — DI de MetadataStore, RateCard, EventBus,
vault_dir y sleeper. Los tests acelaran el retry con sleeper=fast no-op.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.platform.analytics.bus import EventBus
from src.platform.analytics.events import AnalyticsEvent
from src.platform.state import FilesystemMetadataStore
from src.platform.whatsapp.cost import (
    RateCard,
    RateCardEntry,
)
from src.plugins.chats.agent.sales.use_cases.ingest_delivery_status import (
    IngestDeliveryStatus,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def co_rate_card() -> RateCard:
    """Rate card Colombia Q2 2026 — mismo que el YAML committed."""
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
    """Directorio aislado del vault (alternativo al WORKSPACE_VAULT_DIR autouse)."""
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    return vault


class _RecordingSink:
    """Sink in-memory para verificar emisión de analytics."""

    name = "recording"

    def __init__(self) -> None:
        self.events: list[AnalyticsEvent] = []

    async def write(self, event: AnalyticsEvent) -> None:
        self.events.append(event)


@pytest.fixture
def event_bus() -> tuple[EventBus, _RecordingSink]:
    sink = _RecordingSink()
    bus = EventBus(sinks=[sink])
    return bus, sink


async def _no_sleep(_seconds: float) -> None:
    """Sleeper no-op para acelerar tests de retry."""
    return None


def _make_use_case(
    *,
    vault_dir: Path,
    rate_card: RateCard,
    event_bus: EventBus | None = None,
    sleeper=_no_sleep,
    retry_delays: tuple[float, ...] = (0.001, 0.001, 0.001),
) -> tuple[IngestDeliveryStatus, FilesystemMetadataStore]:
    store = FilesystemMetadataStore(vault_dir)
    use_case = IngestDeliveryStatus(
        metadata_store=store,
        rate_card=rate_card,
        event_bus=event_bus,
        vault_dir=vault_dir,
        sleeper=sleeper,
        retry_delays=retry_delays,
        tenant_id="hubara",
    )
    return use_case, store


def _seed_episode_with_pending_entry(
    store: FilesystemMetadataStore,
    *,
    session_id: str,
    wa_message_id: str,
    kind: str = "template",
    template_name: str | None = "quote_ready_utility_v1",
    closed: bool = False,
    sent_at_ms: int = 1_716_700_100_000,
) -> dict[str, Any]:
    """Persiste un metadata mínimo con UN episodio y UNA pending outbound entry."""
    metadata: dict[str, Any] = {
        "phone_number_id": "PID",
        "episodes": [
            {
                "episode_id": "ep_001",
                "started_at_ms": sent_at_ms - 60_000,
                "started_inbound_message_id": "wamid.IN1",
                "closed_at_ms": sent_at_ms + 3600_000 if closed else None,
                "closing_tag": "COMPRA_EXITOSA" if closed else None,
                "closing_motivo": None,
                "order_id": None,
                "referral_snapshot": None,
                "msgs_count_at_start": 0,
                "msgs_count_at_close": None,
                "outbound_messages": [
                    {
                        "sent_at_ms": sent_at_ms,
                        "wa_message_id": wa_message_id,
                        "kind": kind,
                        "template_name": template_name,
                        "pricing": None,
                        "cost_cents_usd": None,
                        "rate_card_version": None,
                    }
                ],
                "cost_summary": {
                    "total_cents_usd": 0,
                    "messages_count": 1,
                    "messages_billable_count": 0,
                    "messages_free_count": 0,
                    "messages_pending_count": 1,
                    "by_category": {},
                    "by_pricing_type": {},
                },
            }
        ],
    }
    store.write(session_id, metadata)
    return metadata


# =============================================================================
# Escenario 1 — regular marketing → cost computed + summary actualizado
# =============================================================================


@pytest.mark.asyncio
async def test_regular_marketing_materializes_cost_and_summary(
    vault_dir: Path,
    co_rate_card: RateCard,
    event_bus: tuple[EventBus, _RecordingSink],
) -> None:
    bus, sink = event_bus
    use_case, store = _make_use_case(
        vault_dir=vault_dir, rate_card=co_rate_card, event_bus=bus
    )
    session_id = "wa_+5730011112"
    wa_msg = "wamid.MKT1"
    _seed_episode_with_pending_entry(
        store, session_id=session_id, wa_message_id=wa_msg
    )

    await use_case.execute(
        wa_message_id=wa_msg,
        status="delivered",
        pricing={
            "billable": True,
            "pricing_type": "regular",
            "category": "marketing",
        },
    )

    metadata = store.read(session_id)
    entry = metadata["episodes"][0]["outbound_messages"][0]
    assert entry["cost_cents_usd"] == 125  # marketing Colombia Q2 2026
    assert entry["rate_card_version"] == "co_2026q2_v1"
    assert entry["pricing"] == {
        "billable": True,
        "pricing_type": "regular",
        "category": "marketing",
    }

    summary = metadata["episodes"][0]["cost_summary"]
    assert summary["total_cents_usd"] == 125
    assert summary["messages_billable_count"] == 1
    assert summary["messages_free_count"] == 0
    assert summary["messages_pending_count"] == 0
    assert summary["by_category"]["marketing"] == {"count": 1, "cents_usd": 125}
    assert summary["by_pricing_type"]["regular"] == {"count": 1, "cents_usd": 125}

    # Analytics emitido
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.kind == "delivery_status"
    assert event.payload["status"] == "delivered"
    assert event.payload["cost_cents_usd"] == 125
    assert event.payload["category"] == "marketing"
    assert event.correlation["session_id"] == session_id


# =============================================================================
# Escenario 2 — free_customer_service → cost=0 + free_count++
# =============================================================================


@pytest.mark.asyncio
async def test_free_customer_service_increments_free_count(
    vault_dir: Path,
    co_rate_card: RateCard,
    event_bus: tuple[EventBus, _RecordingSink],
) -> None:
    bus, _sink = event_bus
    use_case, store = _make_use_case(
        vault_dir=vault_dir, rate_card=co_rate_card, event_bus=bus
    )
    session_id = "wa_+5730022223"
    wa_msg = "wamid.FREE1"
    _seed_episode_with_pending_entry(
        store, session_id=session_id, wa_message_id=wa_msg, kind="text",
        template_name=None,
    )

    await use_case.execute(
        wa_message_id=wa_msg,
        status="delivered",
        pricing={
            "billable": True,
            "pricing_type": "free_customer_service",
            "category": "service",
        },
    )

    metadata = store.read(session_id)
    entry = metadata["episodes"][0]["outbound_messages"][0]
    assert entry["cost_cents_usd"] == 0  # dentro de ventana 24h → gratis
    assert entry["rate_card_version"] == "co_2026q2_v1"

    summary = metadata["episodes"][0]["cost_summary"]
    assert summary["total_cents_usd"] == 0
    assert summary["messages_billable_count"] == 0
    assert summary["messages_free_count"] == 1
    assert summary["messages_pending_count"] == 0
    assert summary["by_category"]["service"] == {"count": 1, "cents_usd": 0}
    assert summary["by_pricing_type"]["free_customer_service"] == {
        "count": 1,
        "cents_usd": 0,
    }


# =============================================================================
# Escenario 3 — Race: webhook antes que entry exista → retry → dead-letter
# =============================================================================


@pytest.mark.asyncio
async def test_race_no_entry_after_retries_dead_letters(
    vault_dir: Path,
    co_rate_card: RateCard,
    event_bus: tuple[EventBus, _RecordingSink],
) -> None:
    """Webhook arrives before outbound activity persisted entry. Retry max 3x.
    Si sigue sin existir → dead-letter al JSONL."""
    bus, sink = event_bus
    use_case, store = _make_use_case(
        vault_dir=vault_dir,
        rate_card=co_rate_card,
        event_bus=bus,
    )
    # vault está vacío — no hay sesiones, no hay entry
    wa_msg = "wamid.ORPHAN1"

    await use_case.execute(
        wa_message_id=wa_msg,
        status="sent",
        pricing={
            "billable": True,
            "pricing_type": "regular",
            "category": "utility",
        },
    )

    # Dead-letter file existe + tiene una línea
    dead_letter = vault_dir / "_orphan_delivery_statuses.jsonl"
    assert dead_letter.exists()
    lines = dead_letter.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["wa_message_id"] == wa_msg
    assert record["status"] == "sent"
    assert record["reason"] == "not_found"
    assert record["session_id"] is None

    # Analytics emitido aún con session_id=None (audit trail del orphan)
    assert len(sink.events) == 1
    assert sink.events[0].correlation["session_id"] is None


@pytest.mark.asyncio
async def test_race_resolves_on_retry_via_sleeper_side_effect(
    vault_dir: Path,
    co_rate_card: RateCard,
    event_bus: tuple[EventBus, _RecordingSink],
) -> None:
    """Si la entry aparece entre reintentos (side-effect del sleeper que
    simula que F1.7 persistió tarde), el use case la encuentra y materializa."""
    bus, _sink = event_bus
    session_id = "wa_+5730033334"
    wa_msg = "wamid.LATE1"

    # Pre-built store but EMPTY al inicio
    store = FilesystemMetadataStore(vault_dir)
    state: dict[str, int] = {"calls": 0}

    async def sleeper_seeds_entry(_seconds: float) -> None:
        # Después del 2do sleep, F1.7 termina y persiste la entry
        state["calls"] += 1
        if state["calls"] == 2:
            _seed_episode_with_pending_entry(
                store, session_id=session_id, wa_message_id=wa_msg
            )

    use_case = IngestDeliveryStatus(
        metadata_store=store,
        rate_card=co_rate_card,
        event_bus=bus,
        vault_dir=vault_dir,
        sleeper=sleeper_seeds_entry,
        retry_delays=(0.001, 0.001, 0.001),
    )

    await use_case.execute(
        wa_message_id=wa_msg,
        status="delivered",
        pricing={
            "billable": True,
            "pricing_type": "regular",
            "category": "marketing",
        },
    )

    metadata = store.read(session_id)
    entry = metadata["episodes"][0]["outbound_messages"][0]
    assert entry["cost_cents_usd"] == 125
    # NO dead-letter (encontró en retry)
    dead_letter = vault_dir / "_orphan_delivery_statuses.jsonl"
    assert not dead_letter.exists()


# =============================================================================
# Escenario 4 — Duplicate webhook (mismo wa_message_id 2x) → idempotente
# =============================================================================


@pytest.mark.asyncio
async def test_duplicate_webhook_is_idempotent(
    vault_dir: Path,
    co_rate_card: RateCard,
    event_bus: tuple[EventBus, _RecordingSink],
) -> None:
    bus, sink = event_bus
    use_case, store = _make_use_case(
        vault_dir=vault_dir, rate_card=co_rate_card, event_bus=bus
    )
    session_id = "wa_+5730044445"
    wa_msg = "wamid.DUP1"
    _seed_episode_with_pending_entry(
        store, session_id=session_id, wa_message_id=wa_msg
    )

    pricing = {
        "billable": True,
        "pricing_type": "regular",
        "category": "marketing",
    }

    # 1er webhook → materializa
    await use_case.execute(
        wa_message_id=wa_msg, status="delivered", pricing=pricing
    )
    # 2do webhook → idempotente (duplicate)
    await use_case.execute(
        wa_message_id=wa_msg, status="read", pricing=pricing
    )

    metadata = store.read(session_id)
    summary = metadata["episodes"][0]["cost_summary"]
    # Invariantes intactos: NO double-count
    assert summary["total_cents_usd"] == 125
    assert summary["messages_billable_count"] == 1
    assert summary["messages_free_count"] == 0
    assert summary["messages_pending_count"] == 0
    assert summary["by_category"]["marketing"] == {"count": 1, "cents_usd": 125}
    assert summary["by_pricing_type"]["regular"] == {"count": 1, "cents_usd": 125}

    # Invariante global: messages_count == billable + free + pending
    assert summary["messages_count"] == (
        summary["messages_billable_count"]
        + summary["messages_free_count"]
        + summary["messages_pending_count"]
    )

    # Ambas emisiones del event (audit trail completo de todos los webhooks)
    assert len(sink.events) == 2
    assert {ev.payload["status"] for ev in sink.events} == {"delivered", "read"}


# =============================================================================
# Escenario 5 — Episodio cerrado → dead-letter, NO muta summary
# =============================================================================


@pytest.mark.asyncio
async def test_closed_episode_dead_letters_and_does_not_mutate_summary(
    vault_dir: Path,
    co_rate_card: RateCard,
    event_bus: tuple[EventBus, _RecordingSink],
) -> None:
    bus, sink = event_bus
    use_case, store = _make_use_case(
        vault_dir=vault_dir, rate_card=co_rate_card, event_bus=bus
    )
    session_id = "wa_+5730055556"
    wa_msg = "wamid.CLOSED1"
    _seed_episode_with_pending_entry(
        store,
        session_id=session_id,
        wa_message_id=wa_msg,
        closed=True,  # episodio cerrado
    )

    pre_summary = store.read(session_id)["episodes"][0]["cost_summary"]

    await use_case.execute(
        wa_message_id=wa_msg,
        status="delivered",
        pricing={
            "billable": True,
            "pricing_type": "regular",
            "category": "marketing",
        },
    )

    # Dead-letter file existe
    dead_letter = vault_dir / "_orphan_delivery_statuses.jsonl"
    assert dead_letter.exists()
    record = json.loads(dead_letter.read_text(encoding="utf-8").splitlines()[0])
    assert record["reason"] == "episode_closed"
    assert record["session_id"] == session_id
    assert record["episode_id"] == "ep_001"

    # Summary SIN cambios — el episodio cerrado no se muta
    post_summary = store.read(session_id)["episodes"][0]["cost_summary"]
    assert post_summary == pre_summary
    # Entry tampoco se mutó
    entry = store.read(session_id)["episodes"][0]["outbound_messages"][0]
    assert entry["cost_cents_usd"] is None
    assert entry["pricing"] is None

    # Analytics emitido con session_id pero cost_cents_usd=None (dead-letter
    # también es audit trail)
    assert len(sink.events) == 1
    assert sink.events[0].correlation["session_id"] == session_id
    assert sink.events[0].payload["cost_cents_usd"] is None


# =============================================================================
# Escenario 6 — Webhook sin pricing object → persiste status, NO toca cost
# =============================================================================


@pytest.mark.asyncio
async def test_status_without_pricing_does_not_touch_cost(
    vault_dir: Path,
    co_rate_card: RateCard,
    event_bus: tuple[EventBus, _RecordingSink],
) -> None:
    """status=failed (u otro) puede llegar sin pricing object. El use case
    emite el event pero NO computa cost ni muta summary."""
    bus, sink = event_bus
    use_case, store = _make_use_case(
        vault_dir=vault_dir, rate_card=co_rate_card, event_bus=bus
    )
    session_id = "wa_+5730066667"
    wa_msg = "wamid.FAIL1"
    _seed_episode_with_pending_entry(
        store, session_id=session_id, wa_message_id=wa_msg
    )

    pre_summary = store.read(session_id)["episodes"][0]["cost_summary"]

    await use_case.execute(
        wa_message_id=wa_msg,
        status="failed",
        pricing=None,  # no pricing object
    )

    # Entry sigue como pending (cost_cents_usd=None)
    entry = store.read(session_id)["episodes"][0]["outbound_messages"][0]
    assert entry["cost_cents_usd"] is None
    assert entry["pricing"] is None

    # Summary sin cambios
    post_summary = store.read(session_id)["episodes"][0]["cost_summary"]
    assert post_summary == pre_summary

    # Event emitido — para que el dashboard registre el `failed` status
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.payload["status"] == "failed"
    assert event.payload["pricing_type"] is None
    assert event.payload["category"] is None
    assert event.payload["cost_cents_usd"] is None


# =============================================================================
# Sanity — lookup multi-sesión (más de una en el vault)
# =============================================================================


@pytest.mark.asyncio
async def test_lookup_traverses_multiple_sessions(
    vault_dir: Path,
    co_rate_card: RateCard,
    event_bus: tuple[EventBus, _RecordingSink],
) -> None:
    """El scan del vault debe atravesar múltiples sesiones para encontrar
    el wa_message_id correcto. Test crítico — confirma que no nos quedamos
    en la primera sesión que iteramos."""
    bus, _sink = event_bus
    use_case, store = _make_use_case(
        vault_dir=vault_dir, rate_card=co_rate_card, event_bus=bus
    )

    # Sesión A — entry irrelevante
    _seed_episode_with_pending_entry(
        store, session_id="wa_+5730000001", wa_message_id="wamid.AAA"
    )
    # Sesión B — la entry buscada
    _seed_episode_with_pending_entry(
        store, session_id="wa_+5730000002", wa_message_id="wamid.BBB"
    )
    # Sesión C — entry irrelevante
    _seed_episode_with_pending_entry(
        store, session_id="wa_+5730000003", wa_message_id="wamid.CCC"
    )

    await use_case.execute(
        wa_message_id="wamid.BBB",
        status="delivered",
        pricing={
            "billable": True,
            "pricing_type": "regular",
            "category": "utility",
        },
    )

    # Sólo la sesión B mutó
    a = store.read("wa_+5730000001")["episodes"][0]["outbound_messages"][0]
    b = store.read("wa_+5730000002")["episodes"][0]["outbound_messages"][0]
    c = store.read("wa_+5730000003")["episodes"][0]["outbound_messages"][0]
    assert a["cost_cents_usd"] is None
    assert b["cost_cents_usd"] == 8  # utility = 8 cents
    assert c["cost_cents_usd"] is None
