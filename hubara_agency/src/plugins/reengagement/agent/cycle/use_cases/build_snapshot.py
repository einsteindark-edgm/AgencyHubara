"""Plan PURO del snapshot (perfil sync, P-29): metadata → entry del seed.

Sin I/O ni reloj — testeable sin red. El vault scan y el `now_ms` viven en la
activity (`agent/activities/`); acá solo la transformación.
"""
from __future__ import annotations

from typing import Any

from src.sdk.messagingkit import lead_state_from_metadata

#: máximo de toques recientes reportados por conversación (el nodo `plan` del
#: agente solo necesita la ventana de 24h; capamos para no inflar el seed).
RECENT_TOUCHES_CAP = 10


def _recent_touches(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    episodes = metadata.get("episodes") or []
    if not episodes:
        return []
    outbounds = episodes[-1].get("outbound_messages") or []
    touches = [
        {"at_ms": o.get("sent_at_ms"), "kind": o.get("kind")}
        for o in outbounds
        if isinstance(o.get("sent_at_ms"), int)
    ]
    return touches[-RECENT_TOUCHES_CAP:]


def _is_reactivable(metadata: dict[str, Any]) -> bool:
    """Una conversación entra al snapshot si alguna vez hubo actividad
    WhatsApp real (inbound o ventana persistida)."""
    return any(
        isinstance(metadata.get(k), int)
        for k in (
            "last_inbound_at_ms",
            "service_window_expires_at_ms",
            "ctwa_window_expires_at_ms",
        )
    )


def conversation_entry(
    session_id: str, metadata: dict[str, Any]
) -> dict[str, Any] | None:
    """metadata de una sesión → entry del snapshot (None si no reactivable).

    El LeadState va PRE-DIGERIDO (messagingkit — derivación única, Decisión #2
    del plan): el agente nunca re-deriva warmth desde metadata cruda.
    """
    if not _is_reactivable(metadata):
        return None
    lead = lead_state_from_metadata(metadata)
    return {
        "session_id": session_id,
        "service_window_expires_at_ms": metadata.get("service_window_expires_at_ms"),
        "ctwa_window_expires_at_ms": metadata.get("ctwa_window_expires_at_ms"),
        "last_inbound_at_ms": metadata.get("last_inbound_at_ms"),
        "lead": {
            "tag": lead.tag,
            "has_order_draft": lead.has_order_draft,
            "has_registered_order": lead.has_registered_order,
            "is_ctwa_lead": lead.is_ctwa_lead,
            "engaged": lead.engaged,
            "allow_paid_marketing": lead.allow_paid_marketing,
        },
        "recent_touches": _recent_touches(metadata),
    }


def build_snapshot_from_sessions(
    now_ms: int, sessions: list[tuple[str, dict[str, Any]]]
) -> dict[str, Any]:
    """(now_ms, [(session_id, metadata)]) → el seed completo del agente."""
    conversations = [
        entry
        for session_id, metadata in sessions
        if (entry := conversation_entry(session_id, metadata)) is not None
    ]
    return {"schema_version": 1, "now_ms": now_ms, "conversations": conversations}
