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


async def _unconsulted_claims(session_id: str, payload: dict) -> dict:
    """Motor de decisiones (F6): la pregunta de respaldo sobre afirmaciones
    sin consultar, en SOMBRA (nunca actúa). Con el bot de hoy no se pregunta
    nada: devuelve {}."""
    from pathlib import Path

    from src.plugins.chats.agent.sales.decisions.capabilities.texto import Afirmacion
    from src.plugins.chats.agent.sales.decisions.guards import capability, decide_for_session
    from src.sdk.runtime import WORKSPACE_VAULT_DIR

    text = "\n\n".join(t for t in payload.get("sent_texts") or [] if isinstance(t, str) and t.strip())
    if not text:
        return {}
    used = tuple(
        str(s.get("name")) for s in payload.get("steps") or [] if isinstance(s, dict) and s.get("kind") == "tool"
    ) or tuple(str(t.get("name")) for t in payload.get("tools") or [] if isinstance(t, dict) and t.get("name"))
    vault_dir = Path(WORKSPACE_VAULT_DIR)
    verdict = await decide_for_session(
        capability("afirmacion"),
        Afirmacion(text=text, tools_used=used),
        session_id=session_id,
        vault_dir=vault_dir,
    )
    return verdict.to_trace() if verdict.provider != "reglas" else {}


def _workflow_label() -> str | None:
    """`v2` (el workflow nuevo, el bot Jev) o `v1` (el actual): Calidad LLM
    separa por esto. Sale de la activity, no del payload: el workflow no
    cambia (sin comandos nuevos, el replay no se entera)."""
    from src.plugins.chats.agent.sales.decisions.bots import WORKFLOW_V1, WORKFLOW_V2

    return {WORKFLOW_V2: "v2", WORKFLOW_V1: "v1"}.get(activity.info().workflow_type)


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
        workflow = _workflow_label()
        if workflow:
            record["workflow"] = workflow
        claims = await _unconsulted_claims(session_id, payload)
        if claims:
            record["claims"] = claims
        turn_traces.append_trace(WORKSPACE_VAULT_DIR, session_id, record)
    except Exception as exc:  # noqa: BLE001 — la traza nunca bloquea el turno
        activity.logger.warning(
            "persist_turn_trace: no se escribió la traza (session=%s): %r",
            session_id,
            exc,
        )
        return False
    _witness_holes(WORKSPACE_VAULT_DIR, record, metadata)
    return True


def _witness_holes(vault_dir, record: dict, metadata: dict) -> None:
    """Testigo de huecos (`sales/huecos.py`): best-effort, después de la traza."""
    from src.plugins.chats.agent.sales import huecos

    try:
        line = huecos.witness_line(record, metadata)
        if line is not None:
            huecos.append_line(vault_dir, line)
    except Exception as exc:  # noqa: BLE001 — el testigo nunca afecta el turno
        activity.logger.warning("huecos: no se escribió la línea del testigo: %r", exc)
