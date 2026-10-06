"""Builtins del egreso (destinatario, saludo) — PAQUETES_DE_DECISION.md F3.

Se cargan solo cuando una capacidad del paquete los pide (la activity del
egreso y el watchdog del remarketing).
"""
from __future__ import annotations

from typing import Any

from src.plugins.chats.agent.sales.first_contact_greeting import greeting_applies, should_send_first_contact_greeting
from src.sdk.textkit import looks_like_admin_leak

#: Más mensajes que esto no se le preguntan a Jev: decide la regla.
MAX_PARTS = 8


def _numbered(parts: list[str]) -> str:
    return "\n".join(f"[{i}] {part}" for i, part in enumerate(parts, 1))


def admin_leak(inp: Any) -> bool:
    """Huele a parte interno (`looks_like_admin_leak`, con el set que pide la entrada)."""
    return looks_like_admin_leak(inp.text, extended=inp.extended)


def first_contact_greeting(inp: Any) -> bool:
    return should_send_first_contact_greeting(
        first_contact=inp.first_contact, tools_used=list(inp.tools_used), client_texts=list(inp.client_texts)
    )


def greeting_texts(inp: Any) -> str | None:
    """Los textos que recibe el cliente en su primer contacto, solo si el turno
    le manda algo (eso lo sabe el código; Jev solo dice si algo ya saluda)."""
    applies = greeting_applies(
        first_contact=inp.first_contact, tools_used=list(inp.tools_used), client_texts=list(inp.client_texts)
    )
    texts = [t.strip() for t in inp.client_texts if t and t.strip()]
    if not applies or not texts or len(texts) > MAX_PARTS:
        return None
    return "Primer contacto con el cliente. Mensajes que recibe en este turno:\n" + _numbered(texts)
