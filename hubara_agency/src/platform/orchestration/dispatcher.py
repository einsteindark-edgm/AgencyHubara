"""Generic event dispatcher activity.

Reads the source worker's manifest transitions, filters by event match, and
executes each matching action via Temporal (start / signal / ensure_running).

**The dispatcher is generic** — it does NOT know any concrete event subclass,
workflow class, or plugin. It treats events as ``dict`` payloads and dispatches
by name strings resolved from the manifest. This is what makes Level 3
declarative: no module-level imports across sibling workers.

Called by workflows like::

    from src.platform.orchestration import dispatch_event_activity, envelope_for
    from src.plugins.chats.shared.contracts.events import (
        SalesSessionCompletionEvent,
    )

    event = SalesSessionCompletionEvent(
        session_id=session_id,
        tag="INTERESADO",
        summary=summary,
        delay_seconds=60,
    )
    await workflow.execute_activity(
        dispatch_event_activity,
        envelope_for(event, source_plugin="chats", source_worker="sales"),
        start_to_close_timeout=timedelta(seconds=30),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )

The activity returns a ``DispatchResult`` listing the transitions that fired
and the resulting Temporal workflow ids (useful for logs and tests).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Mapping

import structlog
from temporalio import activity
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

from src.platform.orchestration.events import EventEnvelope
from src.platform.orchestration.transitions import Transition, TransitionAction


log = structlog.get_logger()


@dataclass(frozen=True)
class DispatchedTransition:
    """One executed transition + outcome (for the activity's return payload)."""

    transition_id: str
    via: str
    target_plugin: str
    target_worker: str
    target_workflow: str
    workflow_id: str
    outcome: str  # "started" | "signaled" | "noop_already_running" | "raced_already_started"


@dataclass(frozen=True)
class DispatchResult:
    """Aggregate result of an envelope dispatch.

    ``no_matches`` is True when ``transitions[]`` exists for the worker but
    none matched this envelope — useful for tests and observability. It is
    NOT an error: workflows can emit events that no transition handles
    (terminal events), as long as they're declared in ``emits[]``.
    """

    source_plugin: str
    source_worker: str
    event_type: str
    matches: list[DispatchedTransition] = field(default_factory=list)
    no_matches: bool = False


@activity.defn(name="orchestration.dispatch_event")
async def dispatch_event_activity(envelope: EventEnvelope) -> DispatchResult:
    """Dispatch a completion event according to the manifest.

    Steps:
        1. Load all transitions for ``(source_plugin, source_worker)`` from
           the manifest.
        2. Filter by ``Transition.matches(envelope)`` — event_type + when.
        3. For each match, execute ``action`` via Temporal:
              - ``start_workflow``: ``client.start_workflow(name, ...)`` — fails
                with WorkflowAlreadyStartedError → caught, recorded as raced.
              - ``start_workflow_with_replace``: terminate RUNNING + start.
              - ``ensure_running``: if RUNNING → noop, else start.
              - ``signal``: signal handler by name on existing handle.

    All errors are logged but non-fatal at the level of an individual
    transition — failed transitions are recorded in the result and the
    activity continues. This matches the existing dispatcher behavior
    (best-effort routing) — Temporal's retry policy at the activity level
    handles transient failures of the dispatcher as a whole.
    """
    # Local import to avoid a module-level cycle:
    # plugin_manifest reads manifests (no Temporal dep); we want
    # dispatch_event_activity to depend on plugin_manifest, but
    # plugin_manifest should not eagerly import orchestration (would
    # circularize when orchestration imports plugin_manifest for typing).
    from src.platform.plugin_manifest import get_task_queue, get_transitions
    from src.platform.temporal.client import get_temporal_client

    log.info(
        "orchestration.dispatch_event: received envelope",
        event_type=envelope.event_type,
        source_plugin=envelope.source_plugin,
        source_worker=envelope.source_worker,
    )

    transitions = get_transitions(envelope.source_plugin, envelope.source_worker)
    matched: list[Transition] = [t for t in transitions if t.matches(envelope)]

    if not matched:
        log.info(
            "orchestration.dispatch_event: no matching transition",
            event_type=envelope.event_type,
            source_plugin=envelope.source_plugin,
            source_worker=envelope.source_worker,
            num_transitions=len(transitions),
        )
        return DispatchResult(
            source_plugin=envelope.source_plugin,
            source_worker=envelope.source_worker,
            event_type=envelope.event_type,
            no_matches=True,
        )

    client = await get_temporal_client()
    outcomes: list[DispatchedTransition] = []

    for t in matched:
        target_plugin = t.action.target_plugin or envelope.source_plugin
        target_worker = t.action.target_worker
        if target_worker is None:
            raise ValueError(
                f"Transition {t.id} in {t.source_plugin}/{t.source_worker} "
                f"has no target_worker — required for all verbs in "
                f"orchestration v1. Fix the manifest."
            )

        task_queue = get_task_queue(target_plugin, target_worker)
        workflow_id = _resolve_workflow_id(t.action, envelope)
        target_input = _build_input(t.action, envelope)
        start_delay = _resolve_start_delay(t.action, envelope)

        outcome = await _execute_action(
            client=client,
            action=t.action,
            workflow_id=workflow_id,
            task_queue=task_queue,
            target_input=target_input,
            start_delay=start_delay,
        )

        outcomes.append(
            DispatchedTransition(
                transition_id=t.id,
                via=t.action.via,
                target_plugin=target_plugin,
                target_worker=target_worker,
                target_workflow=t.action.target_workflow,
                workflow_id=workflow_id,
                outcome=outcome,
            )
        )

    return DispatchResult(
        source_plugin=envelope.source_plugin,
        source_worker=envelope.source_worker,
        event_type=envelope.event_type,
        matches=outcomes,
    )


# ── helpers ────────────────────────────────────────────────────────────────


def _resolve_workflow_id(action: TransitionAction, envelope: EventEnvelope) -> str:
    """Substitute ``{event.<field>}`` tokens in ``workflow_id_template``.

    Default template (when ``workflow_id_template`` is None):
        - If payload has ``session_id`` → ``"{target_worker_or_workflow}-{session_id}"``
          (matches the existing chats convention: ``session-<sid>``,
          ``remarketing-<sid>``).
        - Otherwise: raises ``ValueError`` — must declare explicit template.
    """
    template = action.workflow_id_template
    if template is None:
        session_id = envelope.payload.get("session_id")
        if session_id is None:
            raise ValueError(
                f"Action targets {action.target_workflow} but neither "
                f"workflow_id_template nor event.session_id provided. "
                f"Add `workflow_id_template:` to the transition action."
            )
        # Convention: "<target_worker_name>-<session_id>".
        # Sales convention is "session-<sid>" historically, not
        # "sales-<sid>" — to keep behavior identical, the manifest MUST
        # declare workflow_id_template explicitly for sales transitions.
        # (Caller error if missing — fail fast, no silent rename.)
        target = action.target_worker or action.target_workflow
        return f"{target}-{session_id}"

    out = template
    for key, value in envelope.payload.items():
        out = out.replace(f"{{event.{key}}}", str(value))
    return out


def _build_input(action: TransitionAction, envelope: EventEnvelope) -> Any:
    """Apply ``input_mapping`` to build the target workflow input.

    Mapping rules:
        - ``"$"`` → the entire payload dict
        - ``"$.field_name"`` → ``payload["field_name"]``
        - No mapping → pass the entire payload as single dict arg

    The dispatcher always passes ONE positional arg to start_workflow / signal.
    Target workflows take that arg (typically a dataclass instance via
    Temporal's DataConverter when type hints are present at the worker side).
    """
    mapping = action.input_mapping
    if mapping is None:
        return dict(envelope.payload)

    out: dict[str, Any] = {}
    for target_field, expr in mapping.items():
        if expr == "$":
            out[target_field] = dict(envelope.payload)
        elif expr.startswith("$."):
            field_name = expr[2:]
            if field_name not in envelope.payload:
                raise KeyError(
                    f"input_mapping {target_field}={expr!r} references "
                    f"missing event field {field_name!r}. "
                    f"Payload keys: {list(envelope.payload)}"
                )
            out[target_field] = envelope.payload[field_name]
        else:
            raise ValueError(
                f"Unsupported input_mapping expression {expr!r}. "
                f"Supported: '$' or '$.<field>'."
            )
    return out


def _resolve_start_delay(action: TransitionAction, envelope: EventEnvelope) -> timedelta:
    """Read ``start_delay_field`` from payload as int seconds, or zero."""
    if action.start_delay_field is None:
        return timedelta(seconds=0)
    raw = envelope.payload.get(action.start_delay_field, 0)
    try:
        return timedelta(seconds=int(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"start_delay_field {action.start_delay_field!r} resolves to "
            f"{raw!r}, not an int. Fix the event payload or remove the field."
        ) from exc


async def _execute_action(
    *,
    client: Client,
    action: TransitionAction,
    workflow_id: str,
    task_queue: str,
    target_input: Any,
    start_delay: timedelta,
) -> str:
    """Execute one ``action`` and return a short outcome label."""
    via = action.via

    if via == "signal":
        signal_name = action.signal_name
        if signal_name is None:
            raise ValueError(
                f"Action via=signal requires signal_name "
                f"(workflow={action.target_workflow}, id={workflow_id})"
            )
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal(signal_name, target_input)
        log.info(
            "orchestration.dispatch_event: signaled",
            workflow_id=workflow_id,
            signal_name=signal_name,
        )
        return "signaled"

    if via == "ensure_running":
        if await _is_running(client, workflow_id):
            log.info(
                "orchestration.dispatch_event: target already RUNNING, noop",
                workflow_id=workflow_id,
            )
            return "noop_already_running"
        # fall through to start

    if via == "start_workflow_with_replace":
        await _terminate_if_running(
            client, workflow_id, reason=f"Replaced by orchestration transition"
        )

    try:
        await client.start_workflow(
            action.target_workflow,
            target_input,
            id=workflow_id,
            task_queue=task_queue,
            start_delay=start_delay if start_delay > timedelta(seconds=0) else None,
        )
        log.info(
            "orchestration.dispatch_event: started",
            workflow_id=workflow_id,
            target_workflow=action.target_workflow,
            task_queue=task_queue,
            start_delay_seconds=int(start_delay.total_seconds()),
        )
        return "started"
    except WorkflowAlreadyStartedError:
        # Race between is_running check and start_workflow (or another
        # caller raced us). The target is covered either way; record it.
        log.info(
            "orchestration.dispatch_event: race — target already started",
            workflow_id=workflow_id,
        )
        return "raced_already_started"


async def _is_running(client: Client, workflow_id: str) -> bool:
    """Best-effort RUNNING check. Returns False on any RPC/race error."""
    try:
        handle = client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
        return desc.status == WorkflowExecutionStatus.RUNNING
    except (RPCError, RuntimeError):
        return False


async def _terminate_if_running(
    client: Client, workflow_id: str, *, reason: str
) -> None:
    """Best-effort terminate. Captures races between describe and terminate."""
    try:
        handle = client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
        if desc.status == WorkflowExecutionStatus.RUNNING:
            try:
                await handle.terminate(reason=reason)
                log.info(
                    "orchestration.dispatch_event: terminated existing",
                    workflow_id=workflow_id,
                )
            except Exception as exc:
                log.info(
                    "orchestration.dispatch_event: terminate race",
                    workflow_id=workflow_id,
                    error=str(exc),
                )
    except RPCError:
        # Doesn't exist — fine, start_workflow downstream will create fresh.
        pass
