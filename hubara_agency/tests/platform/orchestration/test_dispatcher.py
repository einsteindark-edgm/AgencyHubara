"""Unit tests for ``src.platform.orchestration.dispatcher``.

The dispatcher is the runtime piece of Level 3 — it accepts an envelope,
looks up transitions in the manifest, and calls Temporal. Tests here mock
Temporal entirely; the focus is the routing / verb / mapping logic.

Each test sets up a tiny fake manifest by patching
``src.platform.plugin_manifest.get_transitions`` (and ``get_task_queue``) and
a fake ``Client`` that records what was called. No real Temporal connection.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from src.platform.orchestration import dispatch_event_activity, envelope_for
from src.platform.orchestration.transitions import Transition


@dataclass(frozen=True)
class _Event:
    session_id: str
    tag: str = ""
    motivo: str = ""
    delay_seconds: int = 0


def _make_transition(
    *,
    via: str,
    when: dict[str, Any] | None = None,
    target_workflow: str = "TargetWorkflow",
    target_plugin: str = "chats",
    target_worker: str = "remarketing",
    signal_name: str | None = None,
    workflow_id_template: str | None = None,
    input_mapping: dict[str, str] | None = None,
    start_delay_field: str | None = None,
    on_event: str = "_Event",
    id: str = "t1",
) -> Transition:
    return Transition.from_dict(
        {
            "id": id,
            "on_event": on_event,
            "when": when or {},
            "action": {
                "via": via,
                "target_workflow": target_workflow,
                "target_plugin": target_plugin,
                "target_worker": target_worker,
                **({"signal_name": signal_name} if signal_name else {}),
                **({"workflow_id_template": workflow_id_template} if workflow_id_template else {}),
                **({"input_mapping": input_mapping} if input_mapping else {}),
                **({"start_delay_field": start_delay_field} if start_delay_field else {}),
            },
        },
        source_plugin="chats",
        source_worker="sales",
    )


class _FakeClient:
    """Minimal stand-in for ``temporalio.client.Client``."""

    def __init__(self, *, existing_status: WorkflowExecutionStatus | None = None) -> None:
        # If existing_status is None, get_workflow_handle().describe() raises RPCError.
        # If set, describe() returns that status (RUNNING → handle "exists").
        self._existing_status = existing_status
        # Record every call so tests can assert on them.
        self.started: list[dict[str, Any]] = []
        self.signaled: list[dict[str, Any]] = []
        self.terminated: list[str] = []

    def get_workflow_handle(self, workflow_id: str) -> Any:
        client = self
        existing_status = self._existing_status

        class _Handle:
            async def describe(self):
                if existing_status is None:
                    raise RPCError("not found", None, None)  # type: ignore[arg-type]
                m = MagicMock()
                m.status = existing_status
                return m

            async def signal(self, name, arg):
                client.signaled.append({"id": workflow_id, "name": name, "arg": arg})

            async def terminate(self, reason: str):
                client.terminated.append(workflow_id)

        return _Handle()

    async def start_workflow(
        self,
        workflow_name: str,
        target_input: Any,
        *,
        id: str,
        task_queue: str,
        start_delay: timedelta | None = None,
    ) -> None:
        self.started.append(
            {
                "workflow": workflow_name,
                "input": target_input,
                "id": id,
                "task_queue": task_queue,
                "start_delay": start_delay,
            }
        )


@pytest.fixture
def fake_client_factory(monkeypatch: pytest.MonkeyPatch):
    """Yields a function ``inject(client) -> None`` that wires the fake into the dispatcher."""

    def inject(client: _FakeClient) -> None:
        async def _get_client():
            return client

        monkeypatch.setattr(
            "src.platform.temporal.client.get_temporal_client", _get_client
        )

    return inject


@pytest.fixture
def patch_manifest(monkeypatch: pytest.MonkeyPatch):
    """Yields ``inject(transitions: list, task_queues: dict) -> None``."""

    def inject(
        transitions: list[Transition],
        task_queues: dict[tuple[str, str], str] | None = None,
    ) -> None:
        queues = task_queues or {}

        def fake_get_transitions(plugin: str, worker: str):
            # Match only the source identity we set up in test transitions.
            return [t for t in transitions if (t.source_plugin == plugin and t.source_worker == worker)]

        def fake_get_task_queue(plugin: str, worker: str):
            return queues.get((plugin, worker), f"queue-{plugin}-{worker}")

        monkeypatch.setattr(
            "src.platform.plugin_manifest.get_transitions", fake_get_transitions
        )
        monkeypatch.setattr(
            "src.platform.plugin_manifest.get_task_queue", fake_get_task_queue
        )

    return inject


class TestNoMatches:
    async def test_returns_no_matches_when_event_type_unrecognized(
        self, fake_client_factory, patch_manifest
    ):
        client = _FakeClient()
        fake_client_factory(client)
        patch_manifest([])

        envelope = envelope_for(
            _Event(session_id="abc", tag="INTERESADO"),
            source_plugin="chats",
            source_worker="sales",
        )
        result = await dispatch_event_activity(envelope)

        assert result.no_matches is True
        assert result.matches == []
        assert client.started == []

    async def test_returns_no_matches_when_when_fails(
        self, fake_client_factory, patch_manifest
    ):
        client = _FakeClient()
        fake_client_factory(client)
        patch_manifest([_make_transition(via="start_workflow", when={"tag": "HUMANO"})])

        envelope = envelope_for(
            _Event(session_id="abc", tag="INTERESADO"),
            source_plugin="chats",
            source_worker="sales",
        )
        result = await dispatch_event_activity(envelope)

        assert result.no_matches is True
        assert client.started == []


class TestEnabledPluginsSkip:
    """P-SKIP (PLUGIN_CONTRACT.md §5.4): las transitions a plugins NO habilitados
    se skipean (toggle simétrico, REQ-2). ENABLED_PLUGINS ausente → nunca skipea
    (prod-safe). Cierra el coupling soft orders->chats/eta sin `depends_on` duro.
    """

    async def test_skips_transition_when_target_plugin_disabled(
        self, fake_client_factory, patch_manifest, monkeypatch
    ):
        monkeypatch.setenv("ENABLED_PLUGINS", "chats")  # 'eta' NO habilitado
        client = _FakeClient()
        fake_client_factory(client)
        patch_manifest(
            [
                _make_transition(
                    via="start_workflow",
                    target_plugin="eta",
                    target_worker="eta",
                    workflow_id_template="eta-{event.session_id}",
                )
            ]
        )
        envelope = envelope_for(
            _Event(session_id="abc"),
            source_plugin="chats",
            source_worker="sales",
        )
        result = await dispatch_event_activity(envelope)

        assert client.started == []                  # NO se disparó al vacío
        assert result.matches == []
        assert result.no_matches is False            # SÍ matcheó; se skipeó por disabled
        assert result.skipped_disabled == ["eta/eta"]

    async def test_does_not_skip_when_enabled_plugins_unset(
        self, fake_client_factory, patch_manifest, monkeypatch
    ):
        monkeypatch.delenv("ENABLED_PLUGINS", raising=False)  # None = todos → no skip
        client = _FakeClient()
        fake_client_factory(client)
        patch_manifest(
            [
                _make_transition(
                    via="start_workflow",
                    target_plugin="eta",
                    target_worker="eta",
                    workflow_id_template="eta-{event.session_id}",
                )
            ]
        )
        envelope = envelope_for(
            _Event(session_id="abc"),
            source_plugin="chats",
            source_worker="sales",
        )
        result = await dispatch_event_activity(envelope)

        assert len(client.started) == 1              # se disparó (default prod-safe)
        assert result.skipped_disabled == []

    async def test_fires_when_target_plugin_enabled(
        self, fake_client_factory, patch_manifest, monkeypatch
    ):
        monkeypatch.setenv("ENABLED_PLUGINS", "chats,eta")  # eta habilitado
        client = _FakeClient()
        fake_client_factory(client)
        patch_manifest(
            [
                _make_transition(
                    via="start_workflow",
                    target_plugin="eta",
                    target_worker="eta",
                    workflow_id_template="eta-{event.session_id}",
                )
            ]
        )
        envelope = envelope_for(
            _Event(session_id="abc"),
            source_plugin="chats",
            source_worker="sales",
        )
        result = await dispatch_event_activity(envelope)

        assert len(client.started) == 1
        assert result.skipped_disabled == []


class TestStartWorkflowVerb:
    async def test_starts_with_default_workflow_id_template(
        self, fake_client_factory, patch_manifest
    ):
        client = _FakeClient()
        fake_client_factory(client)
        patch_manifest([_make_transition(via="start_workflow")])

        envelope = envelope_for(
            _Event(session_id="abc"),
            source_plugin="chats",
            source_worker="sales",
        )
        result = await dispatch_event_activity(envelope)

        assert result.no_matches is False
        assert len(client.started) == 1
        call = client.started[0]
        # Default template uses target_worker as the prefix.
        assert call["id"] == "remarketing-abc"
        assert call["workflow"] == "TargetWorkflow"
        assert call["task_queue"] == "queue-chats-remarketing"
        # Without input_mapping, the entire payload is passed as a dict.
        assert call["input"] == {
            "session_id": "abc",
            "tag": "",
            "motivo": "",
            "delay_seconds": 0,
        }
        assert call["start_delay"] is None

    async def test_workflow_id_template_substitutes_tokens(
        self, fake_client_factory, patch_manifest
    ):
        client = _FakeClient()
        fake_client_factory(client)
        patch_manifest(
            [
                _make_transition(
                    via="start_workflow",
                    workflow_id_template="custom-{event.session_id}-{event.tag}",
                )
            ]
        )

        envelope = envelope_for(
            _Event(session_id="sid", tag="X"),
            source_plugin="chats",
            source_worker="sales",
        )
        await dispatch_event_activity(envelope)

        assert client.started[0]["id"] == "custom-sid-X"

    async def test_input_mapping_dollar_root(self, fake_client_factory, patch_manifest):
        client = _FakeClient()
        fake_client_factory(client)
        patch_manifest([_make_transition(via="start_workflow", input_mapping={"full": "$"})])

        envelope = envelope_for(
            _Event(session_id="s", tag="t"),
            source_plugin="chats",
            source_worker="sales",
        )
        await dispatch_event_activity(envelope)

        assert client.started[0]["input"] == {
            "full": {
                "session_id": "s",
                "tag": "t",
                "motivo": "",
                "delay_seconds": 0,
            }
        }

    async def test_input_mapping_field_paths(self, fake_client_factory, patch_manifest):
        client = _FakeClient()
        fake_client_factory(client)
        patch_manifest(
            [
                _make_transition(
                    via="start_workflow",
                    input_mapping={
                        "session_id": "$.session_id",
                        "motivo": "$.motivo",
                    },
                )
            ]
        )

        envelope = envelope_for(
            _Event(session_id="abc", motivo="precio"),
            source_plugin="chats",
            source_worker="sales",
        )
        await dispatch_event_activity(envelope)

        assert client.started[0]["input"] == {"session_id": "abc", "motivo": "precio"}

    async def test_start_delay_field(self, fake_client_factory, patch_manifest):
        client = _FakeClient()
        fake_client_factory(client)
        patch_manifest(
            [_make_transition(via="start_workflow", start_delay_field="delay_seconds")]
        )

        envelope = envelope_for(
            _Event(session_id="x", delay_seconds=42),
            source_plugin="chats",
            source_worker="sales",
        )
        await dispatch_event_activity(envelope)

        assert client.started[0]["start_delay"] == timedelta(seconds=42)

    async def test_records_raced_already_started(self, fake_client_factory, patch_manifest):
        client = _FakeClient()
        client.start_workflow = AsyncMock(  # type: ignore[method-assign]
            side_effect=WorkflowAlreadyStartedError("dup", "id")
        )
        fake_client_factory(client)
        patch_manifest([_make_transition(via="start_workflow")])

        envelope = envelope_for(
            _Event(session_id="x"),
            source_plugin="chats",
            source_worker="sales",
        )
        result = await dispatch_event_activity(envelope)

        assert result.matches[0].outcome == "raced_already_started"


class TestEnsureRunningVerb:
    async def test_noop_when_running(self, fake_client_factory, patch_manifest):
        client = _FakeClient(existing_status=WorkflowExecutionStatus.RUNNING)
        fake_client_factory(client)
        patch_manifest([_make_transition(via="ensure_running")])

        envelope = envelope_for(
            _Event(session_id="x"),
            source_plugin="chats",
            source_worker="sales",
        )
        result = await dispatch_event_activity(envelope)

        assert client.started == []
        assert result.matches[0].outcome == "noop_already_running"

    async def test_starts_when_not_running(self, fake_client_factory, patch_manifest):
        client = _FakeClient(existing_status=None)
        fake_client_factory(client)
        patch_manifest([_make_transition(via="ensure_running")])

        envelope = envelope_for(
            _Event(session_id="x"),
            source_plugin="chats",
            source_worker="sales",
        )
        result = await dispatch_event_activity(envelope)

        assert len(client.started) == 1
        assert result.matches[0].outcome == "started"


class TestStartWorkflowWithReplace:
    async def test_terminates_existing_before_starting(
        self, fake_client_factory, patch_manifest
    ):
        client = _FakeClient(existing_status=WorkflowExecutionStatus.RUNNING)
        fake_client_factory(client)
        patch_manifest([_make_transition(via="start_workflow_with_replace")])

        envelope = envelope_for(
            _Event(session_id="x"),
            source_plugin="chats",
            source_worker="sales",
        )
        await dispatch_event_activity(envelope)

        assert client.terminated == ["remarketing-x"]
        assert len(client.started) == 1


class _SignalRaisingClient(_FakeClient):
    """Fake cuyo ``handle.signal`` lanza una excepción configurable.

    Para testear el path NOT_FOUND del dispatcher (run 3b3fbaee): señalar un
    workflow inexistente debe ser noop, no crash.
    """

    def __init__(self, signal_exc: Exception) -> None:
        super().__init__()
        self._signal_exc = signal_exc

    def get_workflow_handle(self, workflow_id: str) -> Any:
        exc = self._signal_exc

        class _Handle:
            async def describe(self):
                raise RPCError("not found", None, None)  # type: ignore[arg-type]

            async def signal(self, name, arg):
                raise exc

            async def terminate(self, reason: str):
                pass

        return _Handle()


class TestSignalVerb:
    async def test_signals_existing_handle(self, fake_client_factory, patch_manifest):
        client = _FakeClient()
        fake_client_factory(client)
        patch_manifest(
            [
                _make_transition(
                    via="signal",
                    signal_name="send_message",
                    input_mapping={"text": "$.motivo"},
                )
            ]
        )

        envelope = envelope_for(
            _Event(session_id="x", motivo="hola"),
            source_plugin="chats",
            source_worker="sales",
        )
        result = await dispatch_event_activity(envelope)

        assert client.signaled == [
            {"id": "remarketing-x", "name": "send_message", "arg": {"text": "hola"}}
        ]
        assert result.matches[0].outcome == "signaled"

    async def test_signal_to_missing_workflow_is_noop_not_found_status(
        self, fake_client_factory, patch_manifest
    ):
        """Run 3b3fbaee: señalar un workflow inexistente devuelve gRPC
        NOT_FOUND ("sql: no rows in result set"). El dispatcher lo absorbe como
        noop en vez de tumbar TODO el dispatch (antes: el EpisodeClosedEvent
        crasheaba el Sales workflow al cancelar un watchdog que nunca corrió)."""
        client = _SignalRaisingClient(
            RPCError("sql: no rows in result set", RPCStatusCode.NOT_FOUND, None)  # type: ignore[arg-type]
        )
        fake_client_factory(client)
        patch_manifest(
            [
                _make_transition(
                    via="signal",
                    signal_name="cancel_watchdog",
                    input_mapping={"reason": "$.motivo"},
                )
            ]
        )

        envelope = envelope_for(
            _Event(session_id="x", motivo="CONFIRMADO_PAGO_PENDIENTE"),
            source_plugin="chats",
            source_worker="sales",
        )
        # NO debe lanzar.
        result = await dispatch_event_activity(envelope)
        assert result.matches[0].outcome == "noop_target_not_found"

    async def test_signal_to_missing_workflow_is_noop_by_message_signature(
        self, fake_client_factory, patch_manifest
    ):
        """Defensa cross-version: aunque el status no sea exactamente NOT_FOUND,
        la firma textual del backend postgres ('no rows in result set') también
        se trata como target ausente → noop."""
        client = _SignalRaisingClient(
            RPCError("sql: no rows in result set", RPCStatusCode.UNKNOWN, None)  # type: ignore[arg-type]
        )
        fake_client_factory(client)
        patch_manifest(
            [_make_transition(via="signal", signal_name="cancel_watchdog")]
        )

        envelope = envelope_for(
            _Event(session_id="x"),
            source_plugin="chats",
            source_worker="sales",
        )
        result = await dispatch_event_activity(envelope)
        assert result.matches[0].outcome == "noop_target_not_found"

    async def test_signal_transient_rpcerror_propagates(
        self, fake_client_factory, patch_manifest
    ):
        """Un RPCError transitorio (NO not-found) DEBE propagar para que la
        retry policy de la activity de Temporal lo reintente — no lo enmascaramos."""
        client = _SignalRaisingClient(
            RPCError("service unavailable", RPCStatusCode.UNAVAILABLE, None)  # type: ignore[arg-type]
        )
        fake_client_factory(client)
        patch_manifest(
            [_make_transition(via="signal", signal_name="cancel_watchdog")]
        )

        envelope = envelope_for(
            _Event(session_id="x"),
            source_plugin="chats",
            source_worker="sales",
        )
        with pytest.raises(RPCError):
            await dispatch_event_activity(envelope)

    async def test_signal_without_signal_name_raises(
        self, fake_client_factory, patch_manifest
    ):
        client = _FakeClient()
        fake_client_factory(client)
        patch_manifest([_make_transition(via="signal")])  # no signal_name

        envelope = envelope_for(
            _Event(session_id="x"),
            source_plugin="chats",
            source_worker="sales",
        )
        with pytest.raises(ValueError, match="requires signal_name"):
            await dispatch_event_activity(envelope)


class TestErrors:
    async def test_missing_target_worker_raises(self, fake_client_factory, patch_manifest):
        client = _FakeClient()
        fake_client_factory(client)

        # Build a transition manually with target_worker explicitly None.
        bad = Transition.from_dict(
            {
                "id": "bad",
                "on_event": "_Event",
                "action": {"via": "start_workflow", "target_workflow": "X"},
            },
            source_plugin="chats",
            source_worker="sales",
        )
        patch_manifest([bad])

        envelope = envelope_for(
            _Event(session_id="x"),
            source_plugin="chats",
            source_worker="sales",
        )
        with pytest.raises(ValueError, match="has no target_worker"):
            await dispatch_event_activity(envelope)

    async def test_invalid_input_mapping_raises(self, fake_client_factory, patch_manifest):
        client = _FakeClient()
        fake_client_factory(client)
        patch_manifest(
            [_make_transition(via="start_workflow", input_mapping={"x": "bogus"})]
        )

        envelope = envelope_for(
            _Event(session_id="x"),
            source_plugin="chats",
            source_worker="sales",
        )
        with pytest.raises(ValueError, match="Unsupported input_mapping"):
            await dispatch_event_activity(envelope)


# Pytest-asyncio: every coroutine test needs an event loop.
# Add the asyncio mode locally so the suite runs without a global conftest mark.
def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.get_closest_marker("asyncio") is None:
            item.add_marker(pytest.mark.asyncio)
