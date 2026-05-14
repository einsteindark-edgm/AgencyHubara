"""DTOs cross-cutting de boundary (R-JSON).

Aqui viven los dataclasses planos JSON-serializables que cruzan las
fronteras `workflow.execute_activity` para activities compartidas entre
dominios. Pydantic queda fuera (R-JSON).

ADR-001: las tools no abren `temporal_client` ni hacen `start_workflow`.
Devuelven una **decision** que el workflow lee de `tools_used` (y adjuntos)
y aplica via una activity dedicada (`start_or_signal_sales_workflow_activity`,
`schedule_remarketing_workflow_activity`).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TransferDecision:
    """Decision emitida por `TransferToSalesAgentTool`.

    `target_route` queda como string explicito por compatibilidad con `metadata.json`
    (`active_route`). Las constantes se importan desde `src.core.constants`.
    """
    session_id: str
    target_route: str
    summary: str | None = None


@dataclass
class ScheduleRemarketingDecision:
    """Decision emitida por `ManageConversationTagTool` cuando tag = INTERESADO."""
    session_id: str
    motivo: str
    delay_seconds: int = 60


@dataclass(frozen=True)
class EscalationDecision:
    """Decision emitida por `EscalateToHumanTool`.

    El workflow lee este payload, manda el `final_content` de despedida al
    cliente (si no es vacio), persiste el turno y termina (`_force_shutdown`).
    No programa remarketing — escalation a humano significa que el humano
    toma el control y NO queremos que el bot reactive la conversacion.

    `reason_category` queda como string libre (el enum lo valida la tool en
    su schema JSON, no aca) para no acoplar el dataclass a la taxonomia.
    """
    session_id: str
    reason_category: str
    summary: str
