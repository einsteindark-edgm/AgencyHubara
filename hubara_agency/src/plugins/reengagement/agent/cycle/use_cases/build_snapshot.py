"""Plan PURO del snapshot (perfil sync, P-29): metadata → entry del seed.

Sin I/O ni reloj — testeable sin red. El vault scan y el `now_ms` viven en la
activity (`agent/activities/`); acá solo la transformación.
"""
from __future__ import annotations

from typing import Any

from src.sdk.messagingkit import decide_reengagement, lead_state_from_metadata

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
    now_ms: int,
    sessions: list[tuple[str, dict[str, Any]]],
    rate_card: Any = None,
) -> dict[str, Any]:
    """(now_ms, [(session_id, metadata)]) → el seed completo del agente.

    Pre-filtro de escala (Punto 1): con `rate_card`, corre la CENTRAL
    (`decide_reengagement` — la misma autoridad del gate, no un espejo) por
    sesión y EXCLUYE del seed lo que ella suprime (terminales, fase B fría).
    Clasificar millones de fríos en la caja es pagar cómputo por un "no" que
    hubara ya sabe; el seed sigue al conjunto ACCIONABLE, no al vault.
    `prefiltered` cuenta lo excluido por razón — nunca un cap silencioso.
    El agente igual suprime en classify (defensa en profundidad) y el gate
    re-valida al ejecutar.
    """
    conversations: list[dict[str, Any]] = []
    prefiltered: dict[str, int] = {}
    for session_id, metadata in sessions:
        entry = conversation_entry(session_id, metadata)
        if entry is None:
            continue
        if rate_card is not None:
            decision = decide_reengagement(
                now_ms, metadata, lead_state_from_metadata(metadata), rate_card
            )
            if not decision.allowed:
                reason = decision.suppress_reason or "suppressed"
                prefiltered[reason] = prefiltered.get(reason, 0) + 1
                continue
        conversations.append(entry)
    return {
        "schema_version": 1,
        "now_ms": now_ms,
        "conversations": conversations,
        "prefiltered": prefiltered,
    }
