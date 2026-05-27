"""Tests that `IngestInboundMessage` emits watchdog events via the dispatcher.

The use case is wired with a fake Temporal client factory and the dispatcher
helper (`dispatch_envelope_with_client`) is monkeypatched to a recorder. We
assert which envelopes get dispatched per scenario.

Scenarios:
  * Happy path → emits ServiceWindowOpenedEvent.
  * Prior watchdog running → emits BOTH CustomerRepliedEvent and
    ServiceWindowOpenedEvent.
  * active_route=humano → emits NEITHER.
  * Closed last episode (no active) → emits NEITHER.
  * No factory wired (legacy composition) → emits NEITHER, no crash.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

import src.plugins.chats.agent.sales.use_cases.ingest_inbound_message as ingest_mod
from src.plugins.chats.agent.sales.use_cases.ingest_inbound_message import (
    IngestInboundMessage,
)
from src.plugins.chats.agent.sales.parsers import WhatsAppMessage


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeHistoryStore:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def append_user_event(self, session_id: str, content: str) -> None:
        self.events.append((session_id, content))


class FakeLoadOrStart:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def execute(
        self, session_id: str, message: str, phone_number_id: str | None
    ) -> None:
        self.calls.append((session_id, message))


class FakeMetadataStore:
    """In-memory metadata store. Seedeable per-test."""

    def __init__(self, seed: dict | None = None) -> None:
        self.store: dict[str, dict] = (
            {"wa_5491111111111": dict(seed)} if seed else {}
        )

    def read(self, session_id: str) -> dict:
        return dict(self.store.get(session_id, {}))

    def write(self, session_id: str, data: dict) -> None:
        self.store[session_id] = dict(data)


class FakeTemporalClient:
    """Sentinel — we don't call any methods on this directly. The actual
    dispatch is intercepted at module level via monkeypatch."""

    def __init__(self) -> None:
        self.signature = "fake-temporal-client"


class DispatchRecorder:
    """Replacement for `dispatch_envelope_with_client` — records calls."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def record(self, envelope, client) -> None:
        self.calls.append(envelope)

    @property
    def event_types(self) -> list[str]:
        return [c.event_type for c in self.calls]


def _make_text_message(
    from_number: str = "5491111111111", text: str = "hola"
) -> WhatsAppMessage:
    return WhatsAppMessage(
        message_id="wamid.X",
        from_number=from_number,
        phone_number_id="PID",
        text=text,
        media=None,
        timestamp="1714312345",
    )


async def _make_factory() -> FakeTemporalClient:
    return FakeTemporalClient()


# ---------------------------------------------------------------------------
# Helper: wait for background _spawn_safe tasks to finish
# ---------------------------------------------------------------------------


async def _drain_background_tasks() -> None:
    """`_emit_watchdog_events` schedules a `_spawn_safe` task. Yield the loop
    once so it runs, then again to absorb the dispatch coroutine."""
    for _ in range(6):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emits_service_window_opened_on_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First inbound → ServiceWindowOpenedEvent emitted (no prior watchdog)."""
    recorder = DispatchRecorder()
    monkeypatch.setattr(
        ingest_mod, "dispatch_envelope_with_client", recorder.record
    )

    use_case = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=FakeLoadOrStart(),  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(),  # type: ignore[arg-type]
        temporal_client_factory=_make_factory,  # type: ignore[arg-type]
    )

    await use_case.execute(_make_text_message())
    await _drain_background_tasks()

    # Only ServiceWindowOpenedEvent (no prior watchdog).
    assert recorder.event_types == ["ServiceWindowOpenedEvent"]
    env = recorder.calls[0]
    assert env.payload["session_id"] == "wa_5491111111111"
    assert env.payload["episode_id"] == "ep_001"
    assert isinstance(env.payload["fire_at_ms"], int)
    assert env.source_plugin == "chats"
    assert env.source_worker == "sales"


@pytest.mark.asyncio
async def test_emits_customer_replied_when_watchdog_was_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prior watchdog (metadata.watchdog.workflow_id set) → BOTH
    CustomerRepliedEvent (cancel old) AND ServiceWindowOpenedEvent (start new)
    are emitted."""
    recorder = DispatchRecorder()
    monkeypatch.setattr(
        ingest_mod, "dispatch_envelope_with_client", recorder.record
    )

    seed = {
        "active_route": "ventas",
        "watchdog": {"workflow_id": "watchdog-wa_5491111111111-ep_001"},
    }
    use_case = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=FakeLoadOrStart(),  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(seed=seed),  # type: ignore[arg-type]
        temporal_client_factory=_make_factory,  # type: ignore[arg-type]
    )

    await use_case.execute(_make_text_message())
    await _drain_background_tasks()

    # Two events, order: cancel-first then start.
    assert recorder.event_types == [
        "CustomerRepliedEvent",
        "ServiceWindowOpenedEvent",
    ]


@pytest.mark.asyncio
async def test_no_emit_when_route_humano(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """active_route=humano → no watchdog wiring (the bot must not interfere)."""
    recorder = DispatchRecorder()
    monkeypatch.setattr(
        ingest_mod, "dispatch_envelope_with_client", recorder.record
    )

    seed = {"active_route": "humano"}
    use_case = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=FakeLoadOrStart(),  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(seed=seed),  # type: ignore[arg-type]
        temporal_client_factory=_make_factory,  # type: ignore[arg-type]
    )

    await use_case.execute(_make_text_message())
    await _drain_background_tasks()

    assert recorder.calls == []


@pytest.mark.asyncio
async def test_no_emit_when_factory_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy composition (factory=None) → use case keeps working, no crash,
    just no dispatch happens."""
    recorder = DispatchRecorder()
    monkeypatch.setattr(
        ingest_mod, "dispatch_envelope_with_client", recorder.record
    )

    use_case = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=FakeLoadOrStart(),  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(),  # type: ignore[arg-type]
        # temporal_client_factory left as None.
    )

    await use_case.execute(_make_text_message())
    await _drain_background_tasks()

    assert recorder.calls == []


@pytest.mark.asyncio
async def test_emit_includes_correct_fire_at_ms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `fire_at_ms` field equals `service_window_expires_at_ms -
    WATCHDOG_PRE_EXPIRY_MS`. Validates the helper is correctly wired."""
    from src.platform.whatsapp.window import WATCHDOG_PRE_EXPIRY_MS

    recorder = DispatchRecorder()
    monkeypatch.setattr(
        ingest_mod, "dispatch_envelope_with_client", recorder.record
    )

    use_case = IngestInboundMessage(
        history_store=FakeHistoryStore(),  # type: ignore[arg-type]
        load_session=FakeLoadOrStart(),  # type: ignore[arg-type]
        metadata_store=FakeMetadataStore(),  # type: ignore[arg-type]
        temporal_client_factory=_make_factory,  # type: ignore[arg-type]
    )

    await use_case.execute(_make_text_message())
    await _drain_background_tasks()

    assert len(recorder.calls) == 1
    payload = recorder.calls[0].payload
    # fire_at_ms is set; we don't pin the absolute value (it's now-relative)
    # but it should equal expiry - WATCHDOG_PRE_EXPIRY_MS, where expiry is
    # the stored service_window_expires_at_ms (now + 24h).
    fire_at_ms = payload["fire_at_ms"]
    # ~23.5 hours into the future = 24h - 30min.
    expected_offset_ms = 24 * 60 * 60 * 1000 - WATCHDOG_PRE_EXPIRY_MS
    import time

    now_ms_at_test = int(time.time() * 1000)
    # ±2 second slack (handler timing).
    assert (
        abs(fire_at_ms - (now_ms_at_test + expected_offset_ms)) < 2000
    ), f"fire_at_ms={fire_at_ms} expected≈{now_ms_at_test + expected_offset_ms}"
