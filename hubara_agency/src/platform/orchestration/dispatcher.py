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

──── Why no ``from __future__ import annotations`` ────

The dispatcher's return type ``DispatchResult`` contains a nested dataclass
field ``matches: list[DispatchedTransition]``. When the **calling workflow**
receives the activity result, Temporal's default ``DataConverter`` runs
``get_type_hints(DispatchResult)`` to reconstruct the dataclass. With PEP 563
annotations, that triggers an ``eval("list[DispatchedTransition]", ...)`` in
the workflow's sandbox-restricted namespace — which does NOT carry
``DispatchedTransition`` and crashes with ``NameError`` (premortem footgun
F1, confirmed in production on workflow run df5a8fe2-bb7c-4627-b861-dc19643467be).

Without future annotations, hints are evaluated at class-definition time
and stored as real ``types.GenericAlias`` objects — ``get_type_hints`` returns
them directly without any eval. Order matters: ``DispatchedTransition`` (line 53)
MUST be defined before ``DispatchResult`` (line 67). This rule is enforced
by ``test_no_future_annotations_in_temporal_boundary`` (see ``tests/architecture``).
"""
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import structlog
from temporalio import activity
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

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
    outcome: str  # "started" | "signaled" | "noop_already_running" | "raced_already_started" | "noop_target_not_found"


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
    # Transitions cuyo target_plugin NO está habilitado en este deployment
    # (ENABLED_PLUGINS) — skipeadas en vez de disparar al vacío. Cada entry es
    # "target_plugin/target_worker". Vacío cuando ENABLED_PLUGINS está ausente
    # (todos habilitados) → comportamiento idéntico al previo al skip (prod-safe).
    # `list[str]` (builtin) es replay-safe sin eval (ver nota de annotations arriba).
    skipped_disabled: list[str] = field(default_factory=list)


async def dispatch_envelope_with_client(
    envelope: EventEnvelope, client: Client
) -> DispatchResult:
    """Manifest-driven dispatch given an already-acquired Temporal `client`.

    Pure function — same logic as ``dispatch_event_activity`` minus the
    Temporal activity wrapper and minus the implicit ``get_temporal_client``
    call. Exists so HTTP / use case callers (which already hold a client via
    DI) can emit envelopes without spinning up a workflow + activity. The
    activity (`dispatch_event_activity`) is now a thin async wrapper around
    this function.

    The reason for the carve-out: HU-WA24H-001 Sprint 2 emits
    `ServiceWindowOpenedEvent` / `CustomerRepliedEvent` from
    `IngestInboundMessage`, which runs in the FastAPI request handler — not
    inside a workflow. Using the activity from there would require spinning
    up a wrapper workflow just to call one activity, defeating the purpose.

    All side effects (start_workflow / signal / terminate-and-replace) go
    through `_execute_action`, exactly like the activity. Errors at the
    level of an individual transition propagate up — same semantics as the
    activity (no per-transition swallow).
    """
    from src.platform.plugin_manifest import (
        enabled_plugins,
        get_task_queue,
        get_transitions,
    )

    log.info(
        "orchestration.dispatch_event: received envelope",
        event_type=envelope.event_type,
        source_plugin=envelope.source_plugin,
        source_worker=envelope.source_worker,
    )

    enabled = enabled_plugins()  # set[str] | None — None = todos habilitados
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

    outcomes: list[DispatchedTransition] = []
    skipped: list[str] = []

    for t in matched:
        target_plugin = t.action.target_plugin or envelope.source_plugin
        target_worker = t.action.target_worker
        if target_worker is None:
            raise ValueError(
                f"Transition {t.id} in {t.source_plugin}/{t.source_worker} "
                f"has no target_worker — required for all verbs in "
                f"orchestration v1. Fix the manifest."
            )

        # P-SKIP (PLUGIN_CONTRACT.md §5.4): las transitions cross-plugin son SOFT.
        # Si el target_plugin no está habilitado en este deployment, se skipea en
        # vez de disparar al vacío (sin workflow huérfano pending, sin signal
        # NOT_FOUND). `enabled is None` (ENABLED_PLUGINS ausente) → nunca skipea →
        # comportamiento idéntico al previo (prod-safe). Cierra REQ-2 sin
        # `depends_on` duro: ej. `orders` corre standalone aunque `chats`/eta estén
        # apagados — la notificación ETA simplemente no ocurre (degradación limpia).
        if enabled is not None and target_plugin not in enabled:
            log.info(
                "orchestration.dispatch_event: target plugin disabled, skipping transition",
                transition_id=t.id,
                target_plugin=target_plugin,
                target_worker=target_worker,
            )
            skipped.append(f"{target_plugin}/{target_worker}")
            continue

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
        skipped_disabled=skipped,
    )


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

    Implementation: thin wrapper around ``dispatch_envelope_with_client``
    that acquires the Temporal client first. HTTP callers can use the
    underlying function directly.
    """
    from src.platform.temporal.client import get_temporal_client

    client = await get_temporal_client()
    return await dispatch_envelope_with_client(envelope, client)


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

    ──── Important contract: dict → dataclass at the worker boundary ────

    The dispatcher passes a **plain dict** to ``client.start_workflow(name,
    arg, ...)``. Temporal's default ``DataConverter`` reconstructs the
    target's input dataclass on the **worker side** by reading the type
    hint of ``@workflow.run``. This is verified by smoke test (Temporal
    Python SDK 1.25+ — see ``tests/platform/orchestration/test_dict_to_dataclass_contract.py``).

    Consequence: **the target workflow's input dataclass MUST give every
    field a default value, or be present in this dispatch's dict.** If you
    add a non-default field to a dataclass that is the target of any
    transition, you must ALSO either:

      (a) provide it via ``input_mapping`` in every transition that points
          at that workflow, OR
      (b) make the bootstrap activity tolerate its absence (e.g. fallback
          to local config, as Sales/Remarketing's bootstraps do for
          ``runtime_workspace_path``).

    The architecture test ``test_manifest_orchestration_consistency`` partly
    enforces this by requiring ``workflow_classes[]`` to resolve to a real
    class; making the **field-level** check requires AST inspection of the
    target's ``run()`` signature (future enhancement).
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
        try:
            await handle.signal(signal_name, target_input)
        except RPCError as exc:
            # Señalar un workflow que NO existe (nunca arrancó, o ya cerró)
            # devuelve gRPC NOT_FOUND — con el backend postgres el detalle sale
            # como "sql: no rows in result set". NO es el no-op silencioso que
            # los comentarios del manifest asumen. Para signals de cancelación
            # (las únicas via=signal hoy: `cancel_watchdog`) esto es benigno: no
            # hay nada que cancelar. Absorbemos NOT_FOUND para que un target
            # cerrado/ausente no tumbe TODO el dispatch (run 3b3fbaee: un
            # EpisodeClosedEvent crasheó el Sales workflow intentando cancelar un
            # watchdog que nunca corrió). Los RPCError transitorios (UNAVAILABLE,
            # DEADLINE_EXCEEDED, etc.) SIGUEN propagando → Temporal reintenta.
            if _is_not_found(exc):
                log.info(
                    "orchestration.dispatch_event: signal target not found, noop",
                    workflow_id=workflow_id,
                    signal_name=signal_name,
                )
                return "noop_target_not_found"
            raise
        log.info(
            "orchestration.dispatch_event: signaled",
            workflow_id=workflow_id,
            signal_name=signal_name,
        )
        return "signaled"

    if via == "signal_with_start":
        # Atómico (Temporal signal-with-start): si el workflow corre → signal;
        # si no existe / cerró → lo ARRANCA con `target_input` y le entrega el
        # signal. Cierra el hueco del `via: signal` pelado (L-8): un target
        # cerrado por idle/fail descartaba la notificación como noop.
        signal_name = action.signal_name
        if signal_name is None:
            raise ValueError(
                f"Action via=signal_with_start requires signal_name "
                f"(workflow={action.target_workflow}, id={workflow_id})"
            )
        await client.start_workflow(
            action.target_workflow,
            target_input,
            id=workflow_id,
            task_queue=task_queue,
            start_signal=signal_name,
            start_signal_args=[target_input],
        )
        log.info(
            "orchestration.dispatch_event: signaled_with_start",
            workflow_id=workflow_id,
            signal_name=signal_name,
            target_workflow=action.target_workflow,
        )
        return "signaled_with_start"

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
            client, workflow_id, reason="Replaced by orchestration transition"
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


def _is_not_found(exc: RPCError) -> bool:
    """True si un ``RPCError`` indica que el workflow target no existe.

    Temporal devuelve gRPC NOT_FOUND cuando se señala/describe un workflow_id
    sin ejecución (running o cualquiera). Con el backend postgres el detalle
    aflora como ``"sql: no rows in result set"``. Matcheamos el status code
    (robusto) y, defensivamente, esa firma textual por si cambia entre
    versiones del server.
    """
    if getattr(exc, "status", None) == RPCStatusCode.NOT_FOUND:
        return True
    msg = str(getattr(exc, "message", "") or exc).lower()
    return "no rows in result set" in msg or "workflow not found" in msg


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
