"""Plan PURO del snapshot (perfil sync, P-29): metadata → entry del seed.

Sin I/O ni reloj — testeable sin red. El vault scan y el `now_ms` viven en la
activity (`agent/activities/`); acá solo la transformación.
"""
from __future__ import annotations

from typing import Any, Callable

from src.sdk.messagingkit import decide_reengagement, lead_state_from_metadata

#: máximo de toques recientes reportados por conversación (el nodo `plan` del
#: agente solo necesita la ventana de 24h; capamos para no inflar el seed).
RECENT_TOUCHES_CAP = 10

#: dormancia mínima: silencio del cliente antes de ser candidato a reengagement.
#: Post-mortem run 019f6d0d (2026-07-16): sin este umbral, un chat con inbound
#: hace 3.6 min llegaba al agente como candidato — reengagement es para chats
#: abandonados, no para meterse en una conversación de ventas en vivo. NO va en
#: la central (`decide_reengagement`): ella también sirve al trigger manual
#: del dashboard (delay 0).
#:
#: ESCALERA POR CALOR (mejores prácticas de carrito abandonado: primer toque
#: a los 30-60 min convierte 20.3% vs 12.2% a las 24h — Rejoiner/Shopify):
#: el umbral es SILENCIO MÍNIMO desde last_inbound, no depende de cuándo cae
#: el ciclo. Con ciclo cada N min, el primer toque cae en [piso, piso+N].
MIN_SILENCE_MS = 4 * 60 * 60 * 1000  # ❄️ cold: sin señal de calor
MIN_SILENCE_WARM_MS = 2 * 60 * 60 * 1000  # 🌡️ INTERESADO o engaged
#: 🔥 gancho transaccional (carrito): 30 min = borde inferior del rango
#: estudiado (30-60). Con el ciclo de 45 min el toque cae en [30, 75] min
#: de silencio. NO bajar de 30: silencios cortos suelen ser el cliente
#: pagando/consultando/tipeando (caso 573229041190) y cada toque gasta 1
#: de los 2 del cadence cap diario.
MIN_SILENCE_HOT_MS = 30 * 60 * 1000

#: espejo del tag (send_policy no exporta INTERESADO como constante; el
#: golden de paridad + los tests de la escalera cazan el drift).
_TAG_INTERESADO = "INTERESADO"


def _min_silence_ms_for(lead: Any) -> int:
    """Piso de silencio según el calor del lead (LeadState pre-digerido).

    El calor NO lo re-analiza un LLM: el agente de ventas ya lo dejó en
    metadata (draft/orden/tag) y `lead_state_from_metadata` lo digiere UNA
    vez (Decisión #2). Espejo en GraphAgents `window_strategist._plan`.
    """
    if lead.transactional_hook:
        return MIN_SILENCE_HOT_MS
    if lead.tag == _TAG_INTERESADO or lead.engaged:
        return MIN_SILENCE_WARM_MS
    return MIN_SILENCE_MS

#: margen tras el último inbound dentro del cual un outbound es RÉPLICA
#: conversacional, no un toque proactivo (post-mortem run 019f6d0d: contar
#: réplicas hacía saltar el cadence_cap para todo lead con conversación real,
#: dejando `csw_free_form` como código muerto en la práctica).
PROACTIVE_TOUCH_GAP_MS = 10 * 60 * 1000


def _recent_touches(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Toques PROACTIVOS del último episodio: outbounds enviados al silencio
    (después de `last_inbound + gap`). Un outbound anterior al último inbound
    fue, por definición, respondido — es conversación, no martilleo. Sin
    inbound registrado, todo outbound es proactivo (conservador)."""
    episodes = metadata.get("episodes") or []
    if not episodes:
        return []
    outbounds = episodes[-1].get("outbound_messages") or []
    last_inbound = metadata.get("last_inbound_at_ms")
    threshold_ms = (
        last_inbound + PROACTIVE_TOUCH_GAP_MS
        if isinstance(last_inbound, int)
        else None
    )
    touches = [
        {"at_ms": o.get("sent_at_ms"), "kind": o.get("kind")}
        for o in outbounds
        if isinstance(o.get("sent_at_ms"), int)
        and (threshold_ms is None or o["sent_at_ms"] > threshold_ms)
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
            # Cierre del último episodio: la supresión already_purchased del
            # espejo (parse-conversations) lo necesita pre-digerido.
            "last_closing_tag": lead.last_closing_tag,
        },
        "recent_touches": _recent_touches(metadata),
    }


def build_snapshot_from_sessions(
    now_ms: int,
    sessions: list[tuple[str, dict[str, Any]]],
    rate_card: Any = None,
    quiet_checker: Callable[[str], bool] | None = None,
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

    Pre-filtro de dormancia (escalera por calor): una conversación con
    inbound más reciente que su piso (`_min_silence_ms_for`) está VIVA
    (ventas en plena charla) — no es candidata a reengagement. Sin
    `last_inbound_at_ms` la dormancia no aplica (nunca escribió; decide la
    central).

    Pre-filtro de quiet hours (política 2026-08-04): de noche (hora LOCAL
    del cliente) el gate suprimiría el envío de todos modos — excluirlos acá
    hace que el seed nocturno salga vacío y el ciclo NO despierte la caja
    EC2. `quiet_checker` viene inyectado por la activity (este módulo es
    puro: sin reloj ni env); None = sin filtro.
    """
    conversations: list[dict[str, Any]] = []
    prefiltered: dict[str, int] = {}
    for session_id, metadata in sessions:
        entry = conversation_entry(session_id, metadata)
        if entry is None:
            continue
        if quiet_checker is not None and quiet_checker(session_id):
            prefiltered["quiet_hours"] = prefiltered.get("quiet_hours", 0) + 1
            continue
        lead = lead_state_from_metadata(metadata)
        last_inbound = metadata.get("last_inbound_at_ms")
        if (
            isinstance(last_inbound, int)
            and now_ms - last_inbound < _min_silence_ms_for(lead)
        ):
            prefiltered["conversation_active"] = (
                prefiltered.get("conversation_active", 0) + 1
            )
            continue
        if rate_card is not None:
            decision = decide_reengagement(now_ms, metadata, lead, rate_card)
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
