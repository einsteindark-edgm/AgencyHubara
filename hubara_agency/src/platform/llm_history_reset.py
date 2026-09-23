"""Corte del historial del LLM al empezar un episodio que lo pide.

Caso 2026-09-22 (runs edbb0d8b / 8e73b7dc): el cliente respondió a una
campaña y el ingest abrió un episodio nuevo, pero el historial del LLM de
exoclaw es por SESIÓN: ventas y remarketing siguieron viendo decenas de turnos
del episodio anterior (la Trilogía) y respondieron sobre eso.

El episodio que lo necesita trae ``llm_history_reset = {applied}`` (lo
escribe el ingest de chats). La primera vez que cada agente arma un prompt en
ese episodio, esta activity mueve el puntero ``last_consolidated`` del
historial de ESE agente al final: lo anterior queda en disco (append-only, el
dashboard no depende de esto) pero ya no viaja al LLM. ``applied`` guarda el
workspace de cada agente que ya cortó → idempotente por agente.

Nada de ``session.metadata["summary"]`` (run 28a8e407, 2026-09-23): exoclaw
lo antepone a CADA mensaje del cliente y lo graba con él, así que un resumen
de una línea terminaba repetido en todo el episodio. Lo del episodio anterior
viaja UNA vez, en el primer mensaje del episodio nuevo (lo arma el ingest).
Con la consolidación apagada (#329) exoclaw ya no escribe ``summary``: todo
``summary`` que aparezca lo dejó el corte viejo y se borra en el turno.

Corre en el WORKER (el API no monta el volumen del historial, PR #183).
R-DIP: no importa plugins; el shape del episodio es contrato de datos.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from temporalio import activity

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.state import FilesystemMetadataStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResetLLMHistoryInput:
    """Input de ``reset_llm_history_for_episode_activity`` (R-JSON, frozen)."""

    session_id: str
    #: workspace de CÓDIGO del agente (``SessionInput.workspace.path``): de él
    #: sale el slug del historial en ``EXOCLAW_STATE_DIR``.
    workspace_path: str


def _pending_reset(metadata: dict[str, Any], workspace_path: str) -> dict[str, Any] | None:
    """El pedido de corte del episodio activo si este agente aún no cortó."""
    episodes = metadata.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        return None
    active = episodes[-1]
    if not isinstance(active, dict) or active.get("closed_at_ms") is not None:
        return None
    reset = active.get("llm_history_reset")
    if not isinstance(reset, dict):
        return None
    applied = reset.get("applied")
    if isinstance(applied, list) and workspace_path in applied:
        return None
    return reset


def _session_manager(workspace_path: str) -> Any:
    """Mismo resolver de paths que ``_build_conversation`` (choke point único
    de exoclaw_temporal): con ``EXOCLAW_STATE_DIR`` el historial vive en el
    volumen persistente; sin él (dev/tests), en el workspace de código."""
    from exoclaw_conversation.session.manager import SessionManager
    from exoclaw_temporal.activities.conversation import _state_workspace_for

    code_workspace = Path(workspace_path)
    return SessionManager(_state_workspace_for(code_workspace) or code_workspace)


def _cut_history(session_id: str, workspace_path: str) -> bool:
    """Mueve ``last_consolidated`` al final del historial de este agente (y
    borra cualquier ``summary`` que haya quedado)."""
    manager = _session_manager(workspace_path)
    if not manager._get_session_path(session_id).exists():
        return False
    session = manager.get_or_create(session_id)
    dropped_summary = session.metadata.pop("summary", None) is not None
    cut = session.total_messages > session.last_consolidated
    if cut:
        session.last_consolidated = session.total_messages
    if cut or dropped_summary:
        manager.save_metadata(session)
    return cut


def _drop_leftover_summary(session_id: str, workspace_path: str) -> bool:
    """Borra el ``summary`` que dejó el corte del #330 (sin cortar nada)."""
    manager = _session_manager(workspace_path)
    if not manager._get_session_path(session_id).exists():
        return False
    session = manager.get_or_create(session_id)
    if session.metadata.pop("summary", None) is None:
        return False
    manager.save_metadata(session)
    return True


@activity.defn(name="reset_llm_history_for_episode")
async def reset_llm_history_for_episode_activity(inp: ResetLLMHistoryInput) -> bool:
    """Corta el historial de este agente si el episodio activo lo pide.

    Devuelve True si cortó. Un agente sin historial todavía también queda
    marcado: lo que grabe desde ahora ya es de este episodio.
    """
    store = FilesystemMetadataStore(WORKSPACE_VAULT_DIR)
    reset = _pending_reset(store.read(inp.session_id), inp.workspace_path)
    if reset is None:
        if _drop_leftover_summary(inp.session_id, inp.workspace_path):
            log.info(
                "llm_history_reset summary viejo borrado session=%s workspace=%s",
                inp.session_id,
                inp.workspace_path,
            )
        return False
    cut = _cut_history(inp.session_id, inp.workspace_path)

    def _mark_applied(metadata: dict[str, Any]) -> dict[str, Any] | None:
        fresh = _pending_reset(metadata, inp.workspace_path)
        if fresh is None:
            return None
        applied = fresh.get("applied")
        fresh["applied"] = [*(applied if isinstance(applied, list) else []), inp.workspace_path]
        return metadata

    store.update(inp.session_id, _mark_applied)
    log.info(
        "llm_history_reset session=%s workspace=%s cut=%s",
        inp.session_id,
        inp.workspace_path,
        cut,
    )
    return cut
