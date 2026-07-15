"""MessagingKit — la central de decisión de envío WhatsApp, para plugins.

Fachada SDK (P-28) sobre `src.platform.whatsapp.send_policy` + el rate card:
la ÚNICA forma sancionada de que un plugin consulte SI/CÓMO/CON-QUÉ-COSTO
tocar a un cliente, o derive el `LeadState` de una sesión.

Uso canónico (activity de un plugin)::

    from src.sdk.messagingkit import (
        decide_reengagement,
        get_current_rate_card,
        lead_state_from_metadata,
    )

    lead = lead_state_from_metadata(metadata)
    decision = decide_reengagement(now_ms, metadata, lead, get_current_rate_card())

Consumidores: plugin `reengagement` (snapshot del Window Strategist). La
AUTORIDAD del gasto sigue siendo el gate `check_reengagement_policy` del
RemarketingWorkflow — esta fachada NO habilita a un plugin a mandar por su
cuenta (los sends siguen pasando por las activities de platform).
"""
from __future__ import annotations

from src.platform.whatsapp.composition import (
    get_current_rate_card as get_current_rate_card,
)
from src.platform.whatsapp.quiet_hours import (
    is_quiet_hours_for_session as is_quiet_hours_for_session,
    resolve_local_timezone as resolve_local_timezone,
)
from src.platform.whatsapp import reengagement_index as _reengagement_index

# Índice incremental de reactivación (Punto 2, escala): shortlist para el
# snapshot builder + update en el ingest. Asignaciones (no import-as) para
# que ruff no las pode como unused — son la superficie pública del kit.
load_reengagement_index = _reengagement_index.load_index
update_reengagement_index_entry = _reengagement_index.update_index_entry
update_reengagement_index_entries = _reengagement_index.update_index_entries
reengagement_shortlist = _reengagement_index.shortlist_session_ids

from src.platform.whatsapp.send_policy import (
    LeadState as LeadState,
    SendDecision as SendDecision,
    decide_reengagement as decide_reengagement,
    evaluate_send as evaluate_send,
    lead_state_from_metadata as lead_state_from_metadata,
)

# Guard de ventana de servicio 24h — lo consume el endpoint del operador
# (chats `POST /messages`) para cortar un free-form fuera de ventana antes de
# que Meta lo rechace en silencio. Fail-open cuando la metadata no está poblada.
from src.platform.whatsapp.window import (
    is_service_window_closed as is_service_window_closed,
)
