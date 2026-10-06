"""Notas del turno en el mensaje del turno, no en las instrucciones. PURO.

Medido contra la API de DeepSeek (2026-10-06): su caché es de prefijo estricto
y renderiza las tools DESPUÉS del system. Las notas de cada turno (la hora de
Bogotá al minuto, los DATOS DEL PEDIDO, la nota de la percepción) iban en
`# Retrieved Context`, a mitad de las instrucciones: todo lo que venía detrás
(el guion, la etapa, las 24 tools y el historial) se pagaba sin caché en la
primera llamada de cada turno — 45 % cacheado contra 99 % con las
instrucciones idénticas turno a turno.

Las notas viajan dentro del bloque `[Runtime Context]` que exoclaw antepone al
mensaje del turno, bajo `TURN_NOTES_HEADER` y sin líneas en blanco: al grabar
el historial, exoclaw recorta ese bloque hasta la primera línea en blanco, así
la hora y el pedido de cada turno nunca se acumulan, viejos, en el historial.
"""
from __future__ import annotations

import re
from typing import Any

#: El encabezado con que exoclaw abre el mensaje del turno
#: (`exoclaw_conversation.context._RUNTIME_CONTEXT_TAG`; una prueba vigila que
#: sigan iguales: si exoclaw lo cambia, las notas quedarían en el historial).
RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"

#: Dónde empiezan las notas del turno dentro de ese bloque.
TURN_NOTES_HEADER = "[Notas del sistema para este turno — no las escribió el cliente]"

_BLANK_LINES = re.compile(r"\n[ \t]*\n+")


def _one_block(notes: list[str]) -> str:
    text = "\n".join(n.strip() for n in notes if isinstance(n, str) and n.strip())
    return _BLANK_LINES.sub("\n", text)


def _runtime_part(content: Any) -> bool:
    if isinstance(content, str):
        return content.startswith(RUNTIME_CONTEXT_TAG)
    if isinstance(content, list) and content and isinstance(content[0], dict):
        first = content[0]
        return first.get("type") == "text" and str(first.get("text", "")).startswith(RUNTIME_CONTEXT_TAG)
    return False


def notes_into_turn_message(
    messages: list[dict[str, Any]], notes: list[str] | None
) -> list[dict[str, Any]] | None:
    """`messages` con `notes` dentro del bloque `[Runtime Context]` del mensaje
    del turno (el último). Sin notas, igual. None = no hay dónde ponerlas (el
    último mensaje no es el del turno armado por exoclaw): el caller las deja
    en las instrucciones, como antes."""
    block = _one_block(list(notes or []))
    if not block:
        return list(messages)
    if not messages or messages[-1].get("role") != "user" or not _runtime_part(messages[-1].get("content")):
        return None
    turn = messages[-1]
    content = turn["content"]
    addition = f"\n{TURN_NOTES_HEADER}\n{block}"
    if isinstance(content, str):
        head, sep, rest = content.partition("\n\n")
        moved: Any = f"{head}{addition}{sep}{rest}"
    else:
        moved = [{**content[0], "text": content[0]["text"] + addition}, *content[1:]]
    return [*messages[:-1], {**turn, "content": moved}]


def _split(text: str) -> tuple[str, str]:
    marker = f"\n{TURN_NOTES_HEADER}\n"
    head, sep, rest = text.partition("\n\n")
    at = head.find(marker)
    if at < 0:
        return "", text
    return head[at + len(marker) :], f"{head[:at]}{sep}{rest}"


def turn_notes(content: Any) -> tuple[str, Any]:
    """`(notas, contenido sin ellas)` del mensaje del turno. Sin notas en el
    mensaje: `("", content)` tal cual."""
    if not _runtime_part(content):
        return "", content
    if isinstance(content, str):
        return _split(content)
    notes, text = _split(content[0]["text"])
    if not notes:
        return "", content
    return notes, [{**content[0], "text": text}, *content[1:]]
