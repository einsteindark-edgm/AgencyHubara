"""Eventos del plugin reengagement (canal 2, eventkit).

Sin `from __future__ import annotations`: el envelope cruza el boundary
workflow↔activity y Temporal reconstruye los tipos vía get_type_hints (PEP 563
rompe dentro del sandbox — mismo criterio que send_policy.py).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ReengagementDispatchIntentEvent:
    """UN intent de reactivación emitido por el Window Strategist (R-JSON).

    La transition del manifest lo convierte en un start_workflow del
    RemarketingWorkflow (`remarketing-{session_id}`). Los campos
    `recommended_*` y `reason` son HINTS del agente — la autoridad de
    SI/CÓMO tocar al cliente es el gate `check_reengagement_policy` del
    propio RemarketingWorkflow al ejecutar.

    `motivo` es el texto human-readable que consume RemarketingSessionInput
    (via input_mapping); `run_id` correlaciona el intent con el run del
    agente en la caja GraphAgents (observabilidad).
    """

    session_id: str
    reason: str
    recommended_channel: str
    recommended_category: str
    motivo: str
    run_id: str
    priority: int = 0
