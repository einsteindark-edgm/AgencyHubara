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
y el turno le manda algo al cliente (una tool outbound o un texto) sin que
nada de lo que recibe lleve un saludo, el workflow manda la Burbuja 1 del
guion de apertura (`etapa_descubrimiento`: saludo por hora + propuesta de
valor) ANTES que todo lo demás. Si el LLM ya saludó por el canal legítimo
(`intro_text` / `body` de la tool, o una burbuja de texto), no se duplica.

Turnos de texto (caso del laboratorio, 2026-09-28, CTWA de Halloween): el LLM
saludó JUNTO a `search_products` (descartado) y cerró con un texto sin
saludo. La regla de antes suponía que en un turno de texto el LLM saluda en
su texto; ahora ese turno también lleva la Burbuja 1. `text_turns=False` es la
regla vieja, para las histories en vuelo (el workflow la pide con
`workflow.patched`).

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


def greeting_applies(
    *, first_contact: bool, tools_used: list[str], client_texts: list[str], text_turns: bool = True
) -> bool:
    """¿El turno le manda algo al cliente en su primer contacto? (la parte
    estructural: si aplica, falta saber si algo de lo que recibe ya saluda).

    Args:
        first_contact: el historial que vio el LLM no tenía mensajes del agente.
        tools_used: tools del turno; una outbound (`present_*` / `send_*` /
            `request_*`) le manda algo al cliente.
        client_texts: todo texto que SÍ llega al cliente en este turno
            (burbujas de texto + params de tools outbound).
        text_turns: un texto que sale también cuenta (regla desde el
            2026-09-29); `False` es la regla vieja (solo tools outbound).
    """
    if not first_contact:
        return False
    if any(name.startswith(_OUTBOUND_TOOL_PREFIXES) for name in tools_used):
        return True
    return text_turns and any(t and t.strip() for t in client_texts)


def should_send_first_contact_greeting(
    *, first_contact: bool, tools_used: list[str], client_texts: list[str], text_turns: bool = True
) -> bool:
    """¿El workflow debe mandar la Burbuja 1 antes que el resto del turno?

    Sí cuando el turno le manda algo al cliente en su primer contacto
    (`greeting_applies`) y nada de lo que recibe ya saluda (no se duplica).
    """
    if not greeting_applies(
        first_contact=first_contact, tools_used=tools_used, client_texts=client_texts, text_turns=text_turns
    ):
        return False
    return not any(text_greets(t) for t in client_texts)
