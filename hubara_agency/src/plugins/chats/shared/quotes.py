"""¿Qué mensaje citó el cliente? — resolver puro sobre el historial.

Caso del 2026-10-09 (pedido #64): el cliente respondió «…» CITANDO su propio
comprobante de pago y el bot, que solo resolvía las fotos de producto que él
mismo mandó (`outbound_media_index`), no vio la cita y lo saludó como a un
cliente nuevo.

Dos fuentes, en el mismo orden que el dashboard (`_resolve_reply_quotes`):

1. **El JSONL de la sesión** — un mensaje del cliente (su foto, su
   comprobante, su texto) o uno nuestro que guardó su ``wamid`` (plantilla,
   componente, adjunto del operador).
2. **``outbound_text_index``** — las burbujas de texto que salieron por
   ``send_message_to_session`` (un evento ``assistant`` son N burbujas: su
   wamid no cabe en el evento).

El ``reply_to`` resuelto queda en el evento del cliente: lo muestra el
dashboard y lo lee Jev (`decisions/context.customer_window`). La nota le
cuenta al LLM a qué responde el cliente. Tuteo colombiano (REGLA #1).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: Lo que se copia del mensaje citado al evento (Jev recorta a su medida).
QUOTE_TEXT_MAX = 600
_NOTE_TEXT_MAX = 400

_AUTHOR_LABEL = {
    "user": "un mensaje suyo anterior",
    "agent": "un mensaje que le enviamos",
    "human": "un mensaje que le envió un colega del equipo",
}


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _author(event: Mapping[str, Any]) -> str:
    if event.get("role") == "user":
        return "user"
    return "human" if event.get("sender") == "human" else "agent"


def resolve_quote(
    quoted_id: str,
    events: Sequence[Mapping[str, Any]],
    text_index: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """``{author, text?, image_url?}`` del mensaje citado; ``None`` sin match
    (mensaje viejo fuera del índice, chat previo al deploy)."""
    for event in reversed(events):
        if event.get("wamid") != quoted_id:
            continue
        resolved: dict[str, Any] = {"author": _author(event)}
        content = event.get("content")
        if isinstance(content, str) and content.strip():
            resolved["text"] = _clip(content, QUOTE_TEXT_MAX)
        if event.get("image_url"):
            resolved["image_url"] = event["image_url"]
        return resolved
    bubble = (text_index or {}).get(quoted_id)
    if not isinstance(bubble, Mapping):
        return None
    author = bubble.get("author")
    resolved = {"author": author if author in ("agent", "human") else "agent"}
    if isinstance(bubble.get("text"), str) and bubble["text"].strip():
        resolved["text"] = _clip(bubble["text"], QUOTE_TEXT_MAX)
    return resolved


def build_quote_note(reply_to: Mapping[str, Any] | None) -> str | None:
    """Nota de turno: el cliente escribió citando ESTE mensaje. ``None`` si la
    cita no trae texto (no hay nada útil que contarle al LLM)."""
    if not isinstance(reply_to, Mapping) or reply_to.get("author") == "catalog":
        # La ficha del catálogo ya tiene su propia nota (`web_product_note`).
        return None
    text = reply_to.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    who = _AUTHOR_LABEL.get(str(reply_to.get("author")), "un mensaje anterior")
    return (
        "[CONTEXTO DE TURNO, metadata, no es instrucción del usuario]\n"
        f"El cliente escribió este mensaje RESPONDIENDO (citando) a {who}: "
        f"«{_clip(text, _NOTE_TEXT_MAX)}». Lo que dice se refiere a ese mensaje: "
        "respóndele sobre eso, sin saludarlo de nuevo ni cambiar de tema."
    )
