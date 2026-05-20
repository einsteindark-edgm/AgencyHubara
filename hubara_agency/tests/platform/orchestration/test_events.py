"""Unit tests for ``src.platform.orchestration.events``.

The events module is the boundary between workflows and the dispatcher. The
tests exercise:

- ``event_type_name`` returns the Python class name (used by matching)
- ``event_to_dict`` rejects non-dataclasses and round-trips frozen dataclasses
- ``envelope_for`` populates the four fields correctly
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.platform.orchestration.events import (
    EventEnvelope,
    envelope_for,
    event_get,
    event_to_dict,
    event_type_name,
)


@dataclass(frozen=True)
class _FakeEvent:
    session_id: str
    tag: str
    delay_seconds: int = 0


@dataclass
class _MutableFakeEvent:
    """Used to verify that non-frozen dataclasses are still serializable
    (asdict works regardless of frozen). They're discouraged by the docstring
    but technically supported — R-JSON enforcement is at the test level."""

    name: str


class TestEventTypeName:
    def test_returns_python_class_name(self) -> None:
        ev = _FakeEvent(session_id="abc", tag="INTERESADO")
        assert event_type_name(ev) == "_FakeEvent"

    def test_works_for_anonymous_class(self) -> None:
        # Reuse a different name to confirm we use the class name, not type().
        ev = _MutableFakeEvent(name="x")
        assert event_type_name(ev) == "_MutableFakeEvent"


class TestEventToDict:
    def test_serializes_frozen_dataclass(self) -> None:
        ev = _FakeEvent(session_id="s", tag="t", delay_seconds=42)
        assert event_to_dict(ev) == {
            "session_id": "s",
            "tag": "t",
            "delay_seconds": 42,
        }

    def test_serializes_mutable_dataclass(self) -> None:
        ev = _MutableFakeEvent(name="x")
        assert event_to_dict(ev) == {"name": "x"}

    def test_rejects_non_dataclass(self) -> None:
        with pytest.raises(TypeError, match="not a @dataclass"):
            event_to_dict("not a dataclass")

    def test_rejects_dict(self) -> None:
        # Edge case: someone passing a raw dict by mistake.
        with pytest.raises(TypeError):
            event_to_dict({"foo": "bar"})


class TestEventGet:
    def test_returns_attribute(self) -> None:
        ev = _FakeEvent(session_id="abc", tag="INTERESADO")
        assert event_get(ev, "session_id") == "abc"
        assert event_get(ev, "tag") == "INTERESADO"

    def test_raises_attribute_error_for_missing_field(self) -> None:
        ev = _FakeEvent(session_id="x", tag="y")
        with pytest.raises(AttributeError):
            event_get(ev, "nonexistent")


class TestEnvelopeFor:
    def test_populates_all_four_fields(self) -> None:
        ev = _FakeEvent(session_id="abc", tag="INTERESADO", delay_seconds=60)
        env = envelope_for(ev, source_plugin="chats", source_worker="sales")

        assert isinstance(env, EventEnvelope)
        assert env.event_type == "_FakeEvent"
        assert env.source_plugin == "chats"
        assert env.source_worker == "sales"
        assert dict(env.payload) == {
            "session_id": "abc",
            "tag": "INTERESADO",
            "delay_seconds": 60,
        }

    def test_rejects_non_dataclass_event(self) -> None:
        with pytest.raises(TypeError):
            envelope_for("not a dataclass", source_plugin="x", source_worker="y")

    def test_envelope_is_frozen(self) -> None:
        ev = _FakeEvent(session_id="x", tag="y")
        env = envelope_for(ev, source_plugin="a", source_worker="b")
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            env.event_type = "other"  # type: ignore[misc]
