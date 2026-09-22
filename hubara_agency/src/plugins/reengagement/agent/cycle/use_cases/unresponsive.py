"""Quién queda etiquetado `SIN_RESPUESTA` (puro — sin I/O ni reloj).

Decisión del operador (2026-09-18): agotada la escalera de reactivación no se
envía más — y el cliente queda ETIQUETADO para poder filtrarlo después en el
dashboard y hacer algo con esos números (campaña, llamada, limpieza).

El tag es una ETIQUETA, no un gate: quien corta los envíos es la escalera
(`ladder_state.exhausted`). Por eso no vive en la central ni en el espejo de
GraphAgents. Un inbound nuevo reinicia la escalera (ancla nueva) y el ingest
levanta la etiqueta.
"""
from __future__ import annotations

from typing import Any

from src.sdk.messagingkit import (
    LADDER_GAPS_MS,
    ladder_state,
    lead_state_from_metadata,
)

TAG_UNRESPONSIVE = "SIN_RESPUESTA"

#: Gracia tras el último toque antes de etiquetar: el último hueco de la
#: escalera (6h) — el quinto toque también merece su tiempo de respuesta.
UNRESPONSIVE_GRACE_MS: int = LADDER_GAPS_MS[-1]

#: Solo se re-etiqueta el embudo ABIERTO. Humano, compra, rechazo y los
#: CONFIRMADO_* tienen dueño o significado propio: no se pisan. REMARKETING
#: tampoco: es una decisión HUMANA del dashboard (hallazgo L-5).
_RETAGGABLE: frozenset[str | None] = frozenset(
    {None, "", "NO_ETIQUETADO", "INTERESADO"}
)


def _open(now_ms: int, expires_at_ms: Any) -> bool:
    return isinstance(expires_at_ms, int) and now_ms < expires_at_ms


def _can_continue_for_free(now_ms: int, metadata: dict[str, Any]) -> bool:
    """¿La central dejaría salir otro toque? Con la CSW y la ventana de 72h
    cerradas solo siguen los leads con gancho transaccional (utility barata) u
    opt-in de marketing pago; al resto lo suprime para siempre
    (`fase_b_cold_suppressed`) — no se paga por un lead que no contestó."""
    if _open(now_ms, metadata.get("service_window_expires_at_ms")) or _open(
        now_ms, metadata.get("ctwa_window_expires_at_ms")
    ):
        return True
    lead = lead_state_from_metadata(metadata)
    return lead.transactional_hook or lead.may_pay_marketing


def unresponsive_session_ids(
    now_ms: int, sessions: list[tuple[str, dict[str, Any]]]
) -> list[str]:
    """Sesiones a etiquetar: recibieron toques, no contestaron, y la escalera
    terminó — porque se agotó o porque ya no puede continuar (ventanas gratis
    cerradas a mitad de camino, p.ej. quiet hours empujaron el siguiente toque
    fuera de las 24h). Siempre tras la gracia desde el último toque."""
    out: list[str] = []
    for session_id, metadata in sessions:
        if metadata.get("tag") not in _RETAGGABLE:
            continue
        ladder = ladder_state(now_ms, metadata)
        if (
            ladder.last_touch_at_ms is None
            or now_ms - ladder.last_touch_at_ms < UNRESPONSIVE_GRACE_MS
        ):
            continue
        if ladder.exhausted or not _can_continue_for_free(now_ms, metadata):
            out.append(session_id)
    return out


def mark_unresponsive(metadata: dict[str, Any], *, now_ms: int) -> dict[str, Any]:
    """Aplica la etiqueta (muta y devuelve `metadata`). La ruta NO se toca."""
    motivo = (
        "Escalera de reactivación terminada sin respuesta del cliente. "
        "No se le escribe más."
    )
    metadata["tag"] = TAG_UNRESPONSIVE
    metadata["motivo"] = motivo
    metadata.setdefault("status_history", []).append(
        {
            "tag": TAG_UNRESPONSIVE,
            "motivo": motivo,
            "active_route": metadata.get("active_route"),
            "timestamp": now_ms / 1000,
            "source": "reengagement:ladder",
        }
    )
    return metadata
