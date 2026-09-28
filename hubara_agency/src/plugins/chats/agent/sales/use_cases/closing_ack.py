"""Acuse de despedida tras un cierre (run 4cb3a34f, 2026-09-25).

Ventas cerró el episodio con RECHAZO y se despidió ("…aquí estamos para
ayudarte. ¡Éxitos para ti también!"); 37 s después el cliente mandó "☺️👍". El
ingest lo tomó como una intención nueva: abrió otro episodio con la nota
"saluda con calidez y pregunta en qué puedes ayudar hoy", cortó el historial
del LLM (ya no veía su propia despedida) y el bot arrancó de cero: "Buenas
tardes, bienvenido a *Hubara*… ¿en qué te puedo ayudar hoy?".

Un acuse — emojis, una reacción, un sticker, "gracias", "ok", "igualmente" —
no pide nada: queda en el chat para el operador y no despierta al agente. Un
saludo solo ("Buenas tardes"), una pregunta o un "sí" sí abren conversación.

Funciones puras: el ingest lee el veredicto y persiste.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Callable

from src.plugins.chats.agent.sales.parsers import WhatsAppMessage
from src.plugins.chats.agent.sales.use_cases.episode_memory import (
    unseen_template_text,
)

#: Palabras de un acuse que no pide nada ("gracias 🙏", "ok, igualmente").
#: Sin "sí", "dale" ni "claro": esos le contestan algo al bot.
_ACK_WORDS = frozenset({
    "gracias", "muchas", "mil", "muy", "amable", "ok", "okay", "oki", "listo",
    "vale", "perfecto", "bueno", "super", "genial", "chevere", "excelente",
    "igualmente", "bendiciones", "amen", "con", "gusto", "a", "ti", "usted",
    "ustedes", "tambien", "saludos", "feliz", "chao", "chau", "adios", "de",
    "nada", "pues", "vea",
})
#: Un saludo solo abre conversación ("Buenas tardes"); con una despedida al
#: lado es cierre ("Gracias, buenas noches" / "Feliz tarde").
_TIME_OF_DAY_WORDS = frozenset({
    "buenas", "buenos", "tarde", "tardes", "dia", "dias", "noche", "noches",
})
_FAREWELL_WORDS = frozenset({
    "gracias", "igualmente", "bendiciones", "feliz", "chao", "chau", "adios",
    "saludos",
})
_MAX_WORDS = 6

#: Mensajes que por sí solos son un acuse: reaccionar a la despedida o
#: mandar un sticker.
_ACK_MEDIA_KINDS = frozenset({"reaction", "sticker"})


def _normalize(text: str) -> str:
    stripped = unicodedata.normalize("NFD", text)
    return "".join(c for c in stripped if not unicodedata.combining(c)).lower().strip()


def is_closing_ack(text: str | None) -> bool:
    """True si el texto solo acusa recibo: emojis, gracias, ok, una despedida."""
    if not text:
        return False
    norm = _normalize(text)
    if not norm or "?" in norm or "¿" in norm:
        return False
    words = re.findall(r"[a-z0-9]+", norm)
    if not words:
        # Solo emojis ("☺️👍"); puntuación sola ("...") no cuenta.
        return any(unicodedata.category(c).startswith("S") for c in norm)
    if len(words) > _MAX_WORDS:
        return False
    if any(w not in _ACK_WORDS and w not in _TIME_OF_DAY_WORDS for w in words):
        return False
    if any(w in _TIME_OF_DAY_WORDS for w in words):
        return any(w in _FAREWELL_WORDS for w in words)
    return True


def _is_ack_message(message: WhatsAppMessage) -> bool:
    media_kind = (message.media or {}).get("type")
    if message.msg_type in _ACK_MEDIA_KINDS or media_kind in _ACK_MEDIA_KINDS:
        return True
    if (
        message.media
        or message.interactive
        or message.location
        or message.audio
        or message.order
        or message.contacts
    ):
        return False
    return is_closing_ack(message.text)


def is_ack_after_farewell(
    metadata: dict[str, Any],
    message: WhatsAppMessage,
    read_events: Callable[[], list[dict[str, Any]]],
) -> bool:
    """True si el cliente solo le acusa recibo a la despedida del agente.

    El último episodio tiene que estar cerrado (el agente ya se despidió) y lo
    último que le mandamos no puede ser una plantilla posterior: un "ok" a
    "tu pedido está listo, ¿te lo enviamos hoy?" contesta esa pregunta. La
    respuesta a una campaña la descarta el ingest antes de preguntar acá.
    `read_events` da el JSONL de la sesión ANTES de persistir este mensaje;
    se lee solo si lo demás ya dice que es un acuse.
    """
    episodes = metadata.get("episodes") or []
    if not episodes or episodes[-1].get("closed_at_ms") is None:
        return False
    if not _is_ack_message(message):
        return False
    return unseen_template_text(read_events()) is None
