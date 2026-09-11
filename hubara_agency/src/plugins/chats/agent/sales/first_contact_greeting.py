"""Saludo determinista de primer contacto cuando el turno sale por tool.

Incidente 2026-09-10/11 (runs dc32f7fe y 3ce50ef3, CTWA
"amor y amistad"): el LLM escribió "¡Buenas noches! Bienvenido a *Hubara*..."
como content JUNTO a la tool `search_products` y cerró el turno con
`present_products`. El default-deny (run 1c9ef231) descarta el content que
acompaña tool calls → el cliente recibió el menú SIN saludo. En la session
run a15bb71c (CTWA "velas aromáticas") el LLM respondió solo texto y el
saludo sí salió. La diferencia no es el anuncio: es POR DÓNDE salió el turno.

Mecánica (determinista, no depende del LLM): si es el PRIMER contacto de la
conversación (el historial que vio el LLM no tiene ningún mensaje del agente)
y el turno tocó al cliente vía una tool outbound sin que ningún texto
client-facing lleve un saludo, el workflow manda la Burbuja 1 del guion de
apertura (`etapa_descubrimiento`: saludo por hora + propuesta de valor) ANTES
del flush del menú. Si el LLM ya saludó por el canal legítimo (`intro_text` /
`body` de la tool, o una burbuja de texto), no se duplica.

Helpers puros: sin I/O salvo `datetime.now` cuando `now` es None (la activity
`build_first_contact_greeting` es quien lo invoca — R-DET).
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from src.plugins.chats.agent.sales.context import _BOGOTA_TZ, greeting_for_hour

# Burbuja 1 del guion de apertura (workspace/skills/etapa_descubrimiento).
_VALUE_PROPOSITION = (
    "Bienvenido a *Hubara*, velas artesanales hechas a base de cera de palma, "
    "a mano en Colombia."
)

# Tools que tocan al cliente (send directo o UI intent que el flush entrega).
# Mismo criterio que `_OUTBOUND_TOOL_PREFIXES` en platform.workflow_helpers.
_OUTBOUND_TOOL_PREFIXES: tuple[str, ...] = ("present_", "send_", "request_")

# Señales de que un texto client-facing YA saluda (sin tildes, minúsculas).
_GREETING_PATTERNS = (
    re.compile(r"\bbuenos dias\b"),
    re.compile(r"\bbuenas tardes\b"),
    re.compile(r"\bbuenas noches\b"),
    re.compile(r"\bbienvenid[oa]s?\b"),
    re.compile(r"\bhola\b"),
)


def build_first_contact_greeting(now: datetime | None = None) -> str:
    """Burbuja 1 del guion: saludo según la hora de Bogotá + propuesta de valor."""
    if now is None:
        now = datetime.now(_BOGOTA_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_BOGOTA_TZ)
    else:
        now = now.astimezone(_BOGOTA_TZ)
    return f"¡{greeting_for_hour(now.hour)}! {_VALUE_PROPOSITION}"


def _normalize(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text)
    return "".join(c for c in stripped if not unicodedata.combining(c)).lower()


def text_greets(text: str) -> bool:
    """¿Este texto client-facing ya contiene un saludo?"""
    if not text:
        return False
    norm = _normalize(text)
    return any(p.search(norm) for p in _GREETING_PATTERNS)


def should_send_first_contact_greeting(
    *, first_contact: bool, tools_used: list[str], client_texts: list[str]
) -> bool:
    """¿El workflow debe inyectar la Burbuja 1 antes de flushear el turno?

    Args:
        first_contact: el historial que vio el LLM no tenía mensajes del agente.
        tools_used: tools del turno; hace falta al menos una outbound
            (`present_*` / `send_*` / `request_*`) — si el turno fue texto
            solo, el LLM saluda en su propio texto y no hay nada que inyectar.
        client_texts: todo texto que SÍ llega al cliente en este turno
            (burbujas de texto + params de tools outbound). Si alguno ya
            saluda, no se duplica.
    """
    if not first_contact:
        return False
    if not any(name.startswith(_OUTBOUND_TOOL_PREFIXES) for name in tools_used):
        return False
    return not any(text_greets(t) for t in client_texts)
