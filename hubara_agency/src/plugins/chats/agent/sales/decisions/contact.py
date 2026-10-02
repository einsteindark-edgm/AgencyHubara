"""Decisor `contactar` para remarketing (motor de decisiones, F8).

`register_contact_decision()` lo conecta al enchufe de `chats/shared` (lo
llama el worker de remarketing al arrancar): la activity que lee el contexto
del gancho pregunta, antes de redactar, si el toque sobra. El proveedor
(`reglas` = decide el LLM como hoy, `sombra`, `jev`) sale del registro de
bots de la conversación. Sin Temporal.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales.decisions.capabilities.agente import Contacto
from src.plugins.chats.agent.sales.decisions.guards import capability, decide_for_session


async def decide_contact(
    *,
    session_id: str,
    transcript: str,
    touch_number: int | None,
    silence_minutes: int | None,
    vault_dir: Path,
) -> tuple[bool, dict[str, Any]]:
    vault_dir = Path(vault_dir)
    verdict = await decide_for_session(
        capability("contactar"),
        Contacto(transcript=transcript, touch_number=touch_number, silence_minutes=silence_minutes),
        session_id=session_id,
        vault_dir=vault_dir,
    )
    # Con `reglas` el motor no decidió nada: el contexto grabado queda
    # idéntico al de hoy (sin traza).
    return bool(verdict.value), (verdict.to_trace() if verdict.provider != "reglas" else {})


def register_contact_decision() -> None:
    from src.plugins.chats.shared.agent_decisions import register_contact_decider

    register_contact_decider(decide_contact)
