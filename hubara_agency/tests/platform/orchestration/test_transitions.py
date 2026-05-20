"""Unit tests for ``src.platform.orchestration.transitions``.

Verifies:
- ``Transition.from_dict`` parses YAML manifest entries correctly + defaults
- ``Transition.matches`` returns True only when event_type AND every when[k]
  match the envelope.payload
- ``TransitionAction`` carries the full action payload (string fields)

These tests do NOT touch Temporal — they exercise the typed parsing /
matching logic in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.platform.orchestration.events import EventEnvelope, envelope_for
from src.platform.orchestration.transitions import Transition, TransitionAction


@dataclass(frozen=True)
class _FakeEvent:
    session_id: str
    tag: str
    delay_seconds: int = 0


def _env(event, *, source_plugin: str = "chats", source_worker: str = "sales") -> EventEnvelope:
    return envelope_for(event, source_plugin=source_plugin, source_worker=source_worker)


class TestTransitionFromDict:
    def test_parses_minimal_spec(self) -> None:
        spec = {
            "id": "fire",
            "on_event": "_FakeEvent",
            "action": {
                "via": "start_workflow",
                "target_workflow": "MyWorkflow",
                "target_worker": "other",
            },
        }
        t = Transition.from_dict(spec, source_plugin="chats", source_worker="sales")

        assert t.id == "fire"
        assert t.on_event == "_FakeEvent"
        assert dict(t.when) == {}
        assert t.source_plugin == "chats"
        assert t.source_worker == "sales"
        assert t.action.via == "start_workflow"
        assert t.action.target_workflow == "MyWorkflow"
        assert t.action.target_worker == "other"
        # target_plugin defaults to source_plugin
        assert t.action.target_plugin == "chats"

    def test_target_plugin_default_can_be_overridden(self) -> None:
        spec = {
            "id": "cross",
            "on_event": "X",
            "action": {
                "via": "signal",
                "target_workflow": "Y",
                "target_worker": "w",
                "target_plugin": "otra_app",
                "signal_name": "do_it",
            },
        }
        t = Transition.from_dict(spec, source_plugin="chats", source_worker="sales")
        assert t.action.target_plugin == "otra_app"
        assert t.action.signal_name == "do_it"

    def test_parses_full_action(self) -> None:
        spec = {
            "id": "full",
            "on_event": "E",
            "when": {"tag": "X", "delay_seconds": 0},
            "action": {
                "via": "start_workflow_with_replace",
                "target_workflow": "W",
                "target_worker": "wkr",
                "workflow_id_template": "{event.session_id}",
                "input_mapping": {"sid": "$.session_id"},
                "start_delay_field": "delay_seconds",
            },
        }
        t = Transition.from_dict(spec, source_plugin="p", source_worker="s")
        assert dict(t.when) == {"tag": "X", "delay_seconds": 0}
        assert t.action.workflow_id_template == "{event.session_id}"
        assert dict(t.action.input_mapping or {}) == {"sid": "$.session_id"}
        assert t.action.start_delay_field == "delay_seconds"

    def test_raises_on_missing_id(self) -> None:
        with pytest.raises(ValueError, match="missing required field"):
            Transition.from_dict(
                {"on_event": "X", "action": {"via": "start_workflow", "target_workflow": "Y"}},
                source_plugin="p",
                source_worker="s",
            )

    def test_raises_on_missing_on_event(self) -> None:
        with pytest.raises(ValueError, match="missing required field"):
            Transition.from_dict(
                {"id": "t", "action": {"via": "start_workflow", "target_workflow": "Y"}},
                source_plugin="p",
                source_worker="s",
            )

    def test_raises_on_missing_action(self) -> None:
        with pytest.raises(ValueError, match="missing required field"):
            Transition.from_dict(
                {"id": "t", "on_event": "X"},
                source_plugin="p",
                source_worker="s",
            )


class TestTransitionMatches:
    def _make(self, *, on_event: str, when: dict[str, object]) -> Transition:
        return Transition.from_dict(
            {
                "id": "t",
                "on_event": on_event,
                "when": when,
                "action": {"via": "start_workflow", "target_workflow": "W", "target_worker": "wkr"},
            },
            source_plugin="p",
            source_worker="s",
        )

    def test_matches_when_event_type_and_when_match(self) -> None:
        t = self._make(on_event="_FakeEvent", when={"tag": "INTERESADO"})
        ev = _FakeEvent(session_id="x", tag="INTERESADO")
        assert t.matches(_env(ev)) is True

    def test_no_match_when_event_type_differs(self) -> None:
        t = self._make(on_event="OtherEvent", when={"tag": "INTERESADO"})
        ev = _FakeEvent(session_id="x", tag="INTERESADO")
        assert t.matches(_env(ev)) is False

    def test_no_match_when_when_field_differs(self) -> None:
        t = self._make(on_event="_FakeEvent", when={"tag": "INTERESADO"})
        ev = _FakeEvent(session_id="x", tag="HUMANO")
        assert t.matches(_env(ev)) is False

    def test_matches_with_empty_when(self) -> None:
        t = self._make(on_event="_FakeEvent", when={})
        ev = _FakeEvent(session_id="x", tag="HUMANO")
        assert t.matches(_env(ev)) is True

    def test_no_match_when_when_field_missing_in_payload(self) -> None:
        t = self._make(on_event="_FakeEvent", when={"nonexistent": "x"})
        ev = _FakeEvent(session_id="x", tag="t")
        assert t.matches(_env(ev)) is False

    def test_anded_when_multiple_fields(self) -> None:
        t = self._make(on_event="_FakeEvent", when={"tag": "X", "delay_seconds": 30})
        ev_match = _FakeEvent(session_id="s", tag="X", delay_seconds=30)
        ev_partial = _FakeEvent(session_id="s", tag="X", delay_seconds=99)
        assert t.matches(_env(ev_match)) is True
        assert t.matches(_env(ev_partial)) is False

    def test_zero_and_falsy_values_are_compared_equally(self) -> None:
        # delay_seconds=0 should match when=0 (don't treat 0 as "wildcard").
        t = self._make(on_event="_FakeEvent", when={"delay_seconds": 0})
        ev0 = _FakeEvent(session_id="s", tag="t", delay_seconds=0)
        ev1 = _FakeEvent(session_id="s", tag="t", delay_seconds=1)
        assert t.matches(_env(ev0)) is True
        assert t.matches(_env(ev1)) is False


class TestTransitionAction:
    def test_is_frozen(self) -> None:
        a = TransitionAction(via="start_workflow", target_workflow="X")
        with pytest.raises(Exception):
            a.via = "signal"  # type: ignore[misc]

    def test_default_fields_are_none(self) -> None:
        a = TransitionAction(via="start_workflow", target_workflow="X")
        assert a.target_plugin is None
        assert a.target_worker is None
        assert a.signal_name is None
        assert a.workflow_id_template is None
        assert a.input_mapping is None
        assert a.start_delay_field is None
