"""Activity `persist_turn_trace` (HU-SC-0) — escribe la traza del turno.

El workflow arma el payload con lo que solo él sabe (tools con su resultado,
texto enviado o suprimido, guardas que dispararon) usando las funciones puras
de `turn_trace.py`. Esta activity lo enriquece con el estado persistido
(etapa proyectada, draft, señal del cliente, etiquetas y ruta) y lo agrega al
store de trazas de la sesión.

Best-effort: una traza que no se pudo escribir NUNCA afecta al cliente —
devuelve False y el workflow sigue. DEHA: R-JSON (in `str` × 2, out `bool`),
R-STATELESS, P-28 (vault por `src.sdk.runtime`).
"""
from __future__ import annotations

import json
import time

from temporalio import activity

from src.plugins.chats.agent.sales.turn_trace import enrich_turn_trace
from src.plugins.chats.shared import turn_traces


@activity.defn(name="persist_turn_trace")
async def persist_turn_trace_activity(session_id: str, payload_json: str) -> bool:
    from src.sdk.runtime import WORKSPACE_VAULT_DIR, FilesystemMetadataStore

    try:
        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            raise ValueError("payload no es un objeto")
        metadata = FilesystemMetadataStore(WORKSPACE_VAULT_DIR).read(session_id)
        previous = turn_traces.last_trace(WORKSPACE_VAULT_DIR, session_id)
        record = enrich_turn_trace(
            payload,
            metadata,
            previous=previous,
            session_id=session_id,
            recorded_at_ms=int(time.time() * 1000),
        )
        turn_traces.append_trace(WORKSPACE_VAULT_DIR, session_id, record)
    except Exception as exc:  # noqa: BLE001 — la traza nunca bloquea el turno
        activity.logger.warning(
            "persist_turn_trace: no se escribió la traza (session=%s): %r",
            session_id,
            exc,
        )
        return False
    return True
