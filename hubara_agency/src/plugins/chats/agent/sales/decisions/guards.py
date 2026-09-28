"""Guardia del motor para las tools (diseño v2 §03, regla 5): lo ÚNICO que las
tools importan del motor. Sin Temporal (contrato `tools-no-temporal`).

Una tool que hoy decide con una regla de texto (un mapeo a una lista
cerrada, una revisión del texto del LLM) le pide la decisión al motor con
`decide_for_session`: el motor corre la capacidad con el proveedor que el
registro de bots dice para esa conversación (`reglas`, `sombra` o `jev`),
anota los desacuerdos para que los califique Claude Code y devuelve un
`Verdict`. Con `reglas` (así nace todo) el resultado es el de la regla de hoy
y Jev no se consulta.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales.decisions.bots import bot_for_session
from src.plugins.chats.agent.sales.decisions.capabilities import Verdict, decide
from src.plugins.chats.agent.sales.decisions.disagreements import DisagreementLog

__all__ = ["Verdict", "decide_for_session"]


async def decide_for_session(
    capability: Any,
    inp: Any,
    *,
    session_id: str,
    vault_dir: Path,
    redact: tuple[str, ...] = (),
) -> Verdict:
    """La decisión de la capacidad para ESTA conversación (ver el módulo)."""
    bot = bot_for_session(session_id, vault_dir=Path(vault_dir))
    return await decide(
        capability,
        inp,
        provider=bot.provider(capability.name),
        profile_id=bot.profile,
        disagreements=DisagreementLog(Path(vault_dir)),
        session_id=session_id,
        redact=redact,
    )
