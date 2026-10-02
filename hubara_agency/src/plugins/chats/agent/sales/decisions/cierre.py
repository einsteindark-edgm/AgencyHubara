"""Cierre por abandono (motor de decisiones, F8): la etiqueta del ghosting.

La activity del aviso de ghosting (`decide_ghosting_action`, con la sesión:
solo el workflow V2) le pregunta al motor cómo quedó la conversación. Con
`reglas` (así nace) no decide nada ni lee el vault: el LLM elige la etiqueta
con el aviso de hoy. Sin Temporal.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales.decisions.bots import bot_for_session
from src.plugins.chats.agent.sales.decisions.capabilities.agente import Abandono
from src.plugins.chats.agent.sales.decisions.guards import capability, decide_for_session

#: Lo último de la conversación que ve Jev.
MAX_LINES = 16


def _transcript(vault_dir: Path, session_id: str) -> str:
    path = Path(vault_dir) / session_id / "sessions" / f"{session_id}.jsonl"
    try:
        raw = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    lines: list[str] = []
    for row in raw:
        try:
            event = json.loads(row)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("role") not in ("user", "assistant"):
            continue
        text = " ".join(str(event.get("content") or "").split())
        if not text:
            continue
        who = "cliente" if event["role"] == "user" else ("equipo" if event.get("sender") == "human" else "asesor")
        lines.append(f"[{who}] {text[:400]}")
    return "\n".join(lines[-MAX_LINES:])


def _facts(vault_dir: Path, session_id: str) -> tuple[bool, bool]:
    """(el cliente confirmó la compra, hay pedido registrado) en el episodio."""
    from src.plugins.chats.shared.funnel import active_episode
    from src.plugins.chats.shared.purchase_signals import has_purchase_confirmation

    try:
        metadata: Any = json.loads((Path(vault_dir) / session_id / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, False
    if not isinstance(metadata, dict):
        return False, False
    episode = active_episode(metadata) or {}
    return bool(has_purchase_confirmation(metadata)), bool(episode.get("order_id"))


async def decided_close_tag(session_id: str, *, vault_dir: Path) -> str:
    """La etiqueta que decidió el motor, o "" (decide el LLM, como hoy)."""
    if bot_for_session(session_id, vault_dir=Path(vault_dir)).provider("cierre") == "reglas":
        return ""
    confirmed, registered = _facts(vault_dir, session_id)
    verdict = await decide_for_session(
        capability("cierre"),
        Abandono(transcript=_transcript(vault_dir, session_id), purchase_confirmed=confirmed, order_registered=registered),
        session_id=session_id,
        vault_dir=vault_dir,
    )
    return str(verdict.value or "")


__all__ = ["decided_close_tag"]
