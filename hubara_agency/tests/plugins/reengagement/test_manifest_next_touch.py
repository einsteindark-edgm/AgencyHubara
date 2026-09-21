"""Manifest de `reengagement`: el intent del ciclo llega por `signal_with_start`.

Decisión 2026-09-18 (escalera de reactivación, runs `01a0b0da`…`01a0b586`):
tras el primer gancho el RemarketingWorkflow queda VIVO hasta 24h esperando la
respuesta del cliente. Con `via: start_workflow` el intent del toque N+1 chocaba
con el workflow_id en uso y se descartaba — la escalera era imposible. Con
`signal_with_start`: si el workflow vive recibe `next_touch` (y re-valida todo
antes de tocar); si no existe, arranca y hace su propio primer toque.
NO `_with_replace`: mataría una conversación viva.
"""
from __future__ import annotations

from src.platform.orchestration import envelope_for
from src.platform.plugin_manifest import get_transitions
from src.plugins.reengagement.shared.contracts.events import (
    ReengagementDispatchIntentEvent,
)


def test_dispatch_intent_joins_the_live_remarketing_workflow() -> None:
    env = envelope_for(
        ReengagementDispatchIntentEvent(
            session_id="wa_573001234567",
            reason="csw_free_form",
            recommended_channel="free_form",
            recommended_category="service",
            motivo="Window Strategist: reactivación (csw_free_form)",
            run_id="cycle:run",
        ),
        source_plugin="reengagement",
        source_worker="cycle",
    )
    matching = [t for t in get_transitions("reengagement", "cycle") if t.matches(env)]
    assert len(matching) == 1
    action = matching[0].action
    assert action.via == "signal_with_start"
    assert action.signal_name == "next_touch"
    assert action.target_workflow == "RemarketingWorkflow"
    assert action.workflow_id_template == "remarketing-{event.session_id}"
