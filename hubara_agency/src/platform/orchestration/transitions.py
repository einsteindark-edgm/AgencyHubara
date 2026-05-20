"""Typed representation of ``transitions[]`` entries from the plugin manifest.

The manifest holds the source-of-truth (YAML). At runtime,
``src.platform.plugin_manifest.get_transitions(plugin, worker)`` parses each
entry into ``Transition`` so the dispatcher works against typed objects
instead of raw dicts.

Matching semantics (kept intentionally simple):

- ``Transition.matches(envelope)`` returns True iff
  ``envelope.event_type == self.on_event`` AND every key/value in
  ``self.when`` equals the same field in ``envelope.payload``.
- ``when: {}`` matches any envelope of the right type (no extra constraint).
- No expressions, no eval, no operators. If you need richer logic, set the
  appropriate field on the event in the workflow (it's plain Python there).

This restriction is deliberate (ADR-2026-05-20 §10): allowing arbitrary
expressions in YAML turns the manifest into a mini-language with its own
debugger/security/maintenance burden. Keep the manifest declarative; keep
the logic in code.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from src.platform.orchestration.events import EventEnvelope


Via = Literal[
    "start_workflow",
    "start_workflow_with_replace",
    "ensure_running",
    "signal",
]


@dataclass(frozen=True)
class TransitionAction:
    """The "what to do when the transition fires" half of a transition.

    Mirrors ``transitions[].action`` in the plugin manifest.

    Fields:
        via: dispatch mechanism (see Via literal).
        target_workflow: the Workflow class name (string) registered by the
            target worker. Must appear in the target worker's
            ``workflow_classes[]``.
        target_plugin: plugin id of the target worker. Defaults to the source
            plugin if omitted in YAML (handled by ``Transition.from_dict``).
        target_worker: name of the target worker within ``target_plugin``.
        signal_name: only used when ``via == "signal"`` — the @workflow.signal
            method name (e.g. "send_message").
        workflow_id_template: template for the target workflow id. Tokens
            ``{event.<field>}`` are substituted from the envelope payload.
            If omitted, defaults to ``{event.session_id}`` when the payload
            contains a ``session_id`` (raises otherwise).
        input_mapping: optional map ``target_field -> "$" | "$.<field>"`` to
            shape the input passed to the target workflow. ``$`` = the full
            event payload as a dict (the default if mapping is omitted).
        start_delay_field: name of a payload field (int seconds) used as
            ``start_delay`` for ``start_workflow*`` verbs.
    """

    via: Via
    target_workflow: str
    target_plugin: str | None = None
    target_worker: str | None = None
    signal_name: str | None = None
    workflow_id_template: str | None = None
    input_mapping: Mapping[str, str] | None = None
    start_delay_field: str | None = None


@dataclass(frozen=True)
class Transition:
    """A single ``transitions[]`` entry, fully resolved.

    Created by ``src.platform.plugin_manifest.get_transitions(plugin, worker)``;
    consumed by ``dispatch_event_activity``.

    The ``source_plugin`` / ``source_worker`` fields are populated by the
    loader (not from YAML) so the dispatcher can log which worker produced
    the event without re-deriving it.
    """

    id: str
    on_event: str
    when: Mapping[str, Any]
    action: TransitionAction
    source_plugin: str
    source_worker: str

    def matches(self, envelope: EventEnvelope) -> bool:
        """True iff event_type matches AND every when[k] == payload[k]."""
        if envelope.event_type != self.on_event:
            return False
        for key, expected in self.when.items():
            try:
                actual = envelope.payload[key]
            except KeyError:
                return False
            if actual != expected:
                return False
        return True

    @classmethod
    def from_dict(
        cls,
        spec: Mapping[str, Any],
        *,
        source_plugin: str,
        source_worker: str,
    ) -> "Transition":
        """Parse a raw ``transitions[]`` YAML entry into a typed Transition.

        Defaults applied here:
            - ``when`` defaults to ``{}`` (matches everything of ``on_event``)
            - ``action.target_plugin`` defaults to ``source_plugin``
            - ``action.target_worker`` defaults to ``source_worker`` ONLY for
              ``via=signal`` cases that loop back to the same worker; otherwise
              must be explicit (validation happens in tests).

        Raises ``ValueError`` for missing required fields.
        """
        try:
            tid = spec["id"]
            on_event = spec["on_event"]
            action_spec = spec["action"]
        except KeyError as exc:
            raise ValueError(
                f"transitions[] entry missing required field {exc!s} in "
                f"{source_plugin}/{source_worker}. Got: {dict(spec)!r}"
            ) from exc

        action = TransitionAction(
            via=action_spec["via"],
            target_workflow=action_spec["target_workflow"],
            target_plugin=action_spec.get("target_plugin") or source_plugin,
            target_worker=action_spec.get("target_worker"),
            signal_name=action_spec.get("signal_name"),
            workflow_id_template=action_spec.get("workflow_id_template"),
            input_mapping=action_spec.get("input_mapping"),
            start_delay_field=action_spec.get("start_delay_field"),
        )

        return cls(
            id=tid,
            on_event=on_event,
            when=dict(spec.get("when") or {}),
            action=action,
            source_plugin=source_plugin,
            source_worker=source_worker,
        )
