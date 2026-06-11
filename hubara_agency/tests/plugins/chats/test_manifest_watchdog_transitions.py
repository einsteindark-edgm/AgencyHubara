"""Tests for HU-WA24H-001 Sprint 2 manifest extensions.

Verifies that:
  1. The new event types appear in `emits[]` for both sales and remarketing
     workers.
  2. The 3 watchdog transitions parse correctly and match envelopes:
     * `ServiceWindowOpenedEvent` → `start_workflow_with_replace` targeting
       `ServiceWindowWatchdogWorkflow`.
     * `CustomerRepliedEvent` → `signal` (cancel_watchdog).
     * `EpisodeClosedEvent` → `signal` (cancel_watchdog).
  3. The `ServiceWindowWatchdogWorkflow` is registered in the remarketing
     worker's `workflow_classes[]`.

These tests are complementary to `tests/architecture/test_manifest_orchestration_consistency.py`
which already checks structural validity across ALL plugins. Here we focus
on the *content* added by Sprint 2.
"""
from __future__ import annotations

import pytest

from src.platform.orchestration import envelope_for
from src.platform.plugin_manifest import (
    get_emitted_events,
    get_transitions,
    get_worker_spec,
    load_manifest,
)
from src.plugins.chats.shared.contracts.events import (
    CustomerRepliedEvent,
    EpisodeClosedEvent,
    ServiceWindowOpenedEvent,
)


# ---------------------------------------------------------------------------
# emits[]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("worker_name", ["sales", "remarketing"])
def test_watchdog_events_in_emits(worker_name: str) -> None:
    """Both sales and remarketing workers declare emits for the 3 new
    events — inbound from either side opens a window / cancels watchdog."""
    emits = set(get_emitted_events("chats", worker_name))
    assert "ServiceWindowOpenedEvent" in emits
    assert "CustomerRepliedEvent" in emits
    assert "EpisodeClosedEvent" in emits


# ---------------------------------------------------------------------------
# workflow_classes
# ---------------------------------------------------------------------------


def test_remarketing_worker_registers_watchdog_workflow() -> None:
    spec = get_worker_spec("chats", "remarketing")
    classes = spec.get("workflow_classes") or []
    assert "ServiceWindowWatchdogWorkflow" in classes
    # RemarketingWorkflow still registered (pre-existing).
    assert "RemarketingWorkflow" in classes


# ---------------------------------------------------------------------------
# transitions — sales worker side
# ---------------------------------------------------------------------------


def test_sales_service_window_opened_transition_matches() -> None:
    """ServiceWindowOpenedEvent from sales should match a transition that
    starts the watchdog with the right workflow id template."""
    transitions = get_transitions("chats", "sales")
    env = envelope_for(
        ServiceWindowOpenedEvent(
            session_id="wa_+57300test",
            episode_id="ep_001",
            fire_at_ms=1716700000000,
            suggested_template_kind="quote_pending",
        ),
        source_plugin="chats",
        source_worker="sales",
    )
    matching = [t for t in transitions if t.matches(env)]
    assert len(matching) == 1, f"Expected 1 transition match, got {len(matching)}"
    t = matching[0]
    assert t.action.via == "start_workflow_with_replace"
    assert t.action.target_workflow == "ServiceWindowWatchdogWorkflow"
    assert t.action.target_worker == "remarketing"
    # Workflow id template must include both session and episode.
    assert "{event.session_id}" in (t.action.workflow_id_template or "")
    assert "{event.episode_id}" in (t.action.workflow_id_template or "")


def test_sales_customer_replied_signals_cancel() -> None:
    transitions = get_transitions("chats", "sales")
    env = envelope_for(
        CustomerRepliedEvent(
            session_id="wa_+57300test",
            episode_id="ep_001",
        ),
        source_plugin="chats",
        source_worker="sales",
    )
    matching = [t for t in transitions if t.matches(env)]
    assert len(matching) == 1
    t = matching[0]
    assert t.action.via == "signal"
    assert t.action.signal_name == "cancel_watchdog"
    assert t.action.target_workflow == "ServiceWindowWatchdogWorkflow"


def test_sales_episode_closed_signals_cancel() -> None:
    transitions = get_transitions("chats", "sales")
    env = envelope_for(
        EpisodeClosedEvent(
            session_id="wa_+57300test",
            episode_id="ep_001",
            closing_tag="COMPRA_EXITOSA",
        ),
        source_plugin="chats",
        source_worker="sales",
    )
    matching = [t for t in transitions if t.matches(env)]
    # EpisodeClosedEvent dispara DOS transitions: (1) cancela el watchdog del
    # episodio cerrado, (2) evalúa la calidad de ESE episodio (event-driven).
    assert len(matching) == 2, f"Expected 2 matches, got {len(matching)}"

    cancel = [t for t in matching if t.action.signal_name == "cancel_watchdog"]
    assert len(cancel) == 1
    assert cancel[0].action.via == "signal"

    evals = [
        t for t in matching if t.action.target_workflow == "EvaluateEpisodeWorkflow"
    ]
    assert len(evals) == 1
    assert evals[0].action.via == "start_workflow"
    assert evals[0].action.target_worker == "sales_eval"


# ---------------------------------------------------------------------------
# transitions — remarketing worker side (mirror)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event_type,event_obj_factory",
    [
        (
            "ServiceWindowOpenedEvent",
            lambda: ServiceWindowOpenedEvent(
                session_id="wa_+57300test",
                episode_id="ep_001",
                fire_at_ms=1716700000000,
                suggested_template_kind="",
            ),
        ),
        (
            "CustomerRepliedEvent",
            lambda: CustomerRepliedEvent(
                session_id="wa_+57300test",
                episode_id="ep_001",
            ),
        ),
        (
            "EpisodeClosedEvent",
            lambda: EpisodeClosedEvent(
                session_id="wa_+57300test",
                episode_id="ep_001",
                closing_tag="TIMEOUT",
            ),
        ),
    ],
)
def test_remarketing_worker_mirror_transitions(
    event_type: str, event_obj_factory
) -> None:
    """Same 3 events emitted from remarketing also match transitions (mirror
    of sales side). Decision §5.1 of the refinement keeps the watchdog
    callable from either worker."""
    transitions = get_transitions("chats", "remarketing")
    env = envelope_for(
        event_obj_factory(),
        source_plugin="chats",
        source_worker="remarketing",
    )
    matching = [t for t in transitions if t.matches(env)]
    assert len(matching) == 1, (
        f"Expected 1 remarketing-side transition for {event_type}, "
        f"got {len(matching)}"
    )


# ---------------------------------------------------------------------------
# Defensive: manifest yaml loads without raising
# ---------------------------------------------------------------------------


def test_chats_manifest_loads_clean() -> None:
    """If the YAML is malformed, this raises — guards against typos in the
    transitions block."""
    m = load_manifest("chats")
    assert m["id"] == "chats"
    # Sanity: at least 2 workers (sales + remarketing).
    workers = (m.get("agent") or {}).get("workers", [])
    assert len(workers) >= 2
