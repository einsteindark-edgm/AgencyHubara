"""Contexto REAL para el gancho de remarketing (puro, sin I/O).

Incidente run dc32f7fe (2026-09-10, wa_573114842180): el agente de
remarketing no ve el historial de Sales (el HistoryStore de exoclaw se aísla
por slug de workspace, PR #183) y el ciclo del Window Strategist le pisa el
`motivo` del tag con "Window Strategist: reactivación (<reason>)". Sin
contexto, el LLM inventó "quedó pendiente lo de tu pedido" a un cliente que
quería comprar cera y ya se había despedido.

Acá se digiere lo que el gancho necesita:
  * `tag_motivo`: el motivo que Sales anotó al etiquetar (metadata.motivo).
  * `has_order_draft`: si hay pedido a medias (misma derivación que la
    central: `lead_state_from_metadata`, Decisión #2 del Window Strategist).
  * `transcript`: los últimos mensajes VISIBLES del transcript del vault
    (`<vault>/<sid>/sessions/<sid>.jsonl`, el mismo log que lee el dashboard).
"""
from __future__ import annotations

from typing import Any

from src.plugins.chats.agent.remarketing.contracts import RemarketingContext
from src.sdk.messagingkit import lead_state_from_metadata

#: cuántos mensajes visibles viajan al gancho (WhatsApp: 12 alcanzan para
#: entender por qué se frenó la charla sin inflar el prompt).
TRANSCRIPT_LIMIT = 12

_LABELS = {"user": "Cliente", "assistant": "Asesor"}


def _label(event: dict[str, Any]) -> str | None:
    role = event.get("role")
    if role not in _LABELS:
        return None
    if role == "assistant" and event.get("sender") == "human":
        return "Asesor (humano)"
    return _LABELS[role]


def render_transcript(events: list[dict[str, Any]], *, limit: int = TRANSCRIPT_LIMIT) -> str:
    """Eventos del JSONL → líneas `Cliente: …` / `Asesor: …` (últimas `limit`).

    Salta turnos sin texto visible (tool-calls puros, vacíos): lo que el
    cliente NO vio no ayuda a entender por qué se fue.
    """
    lines: list[str] = []
    for event in events:
        label = _label(event)
        if label is None:
            continue
        content = event.get("content")
        if not isinstance(content, str):
            continue
        text = " ".join(content.split())
        if not text:
            continue
        lines.append(f"{label}: {text}")
    return "\n".join(lines[-limit:]) if limit > 0 else ""


def context_from_metadata(
    metadata: dict[str, Any] | None, events: list[dict[str, Any]]
) -> RemarketingContext:
    """(metadata.json, eventos del transcript) → `RemarketingContext`."""
    meta = metadata or {}
    motivo = meta.get("motivo")
    lead = lead_state_from_metadata(meta)
    return RemarketingContext(
        tag_motivo=motivo.strip() if isinstance(motivo, str) else "",
        has_order_draft=lead.has_order_draft,
        transcript=render_transcript(events),
    )
