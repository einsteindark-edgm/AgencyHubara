"""Lo que el modelo recibe en cada ronda del turno, para la traza (el «Paso a
paso» del laboratorio y el hilo del turno en Chats).

Pedido del operador (2026-09-30, laboratorio 4567 t20): al seleccionar «ronda
N» hacia el modelo «no sabemos qué le está enviando». Función pura sobre la
lista `messages` del tool-loop (R-DET: sin I/O ni reloj):

* primera ronda: el tamaño de cada parte de las instrucciones, las notas del
  turno (van dentro de las instrucciones, `# Retrieved Context`), cuántos
  mensajes trae el historial por rol y el mensaje del cliente tal como lo lee
  el modelo;
* rondas siguientes: solo lo nuevo — los resultados de las herramientas y las
  notas del bot (lo que respondió el modelo ya está en la ronda anterior).

Acotado: la traza viaja en el input de una activity (history de Temporal). El
número del cliente (`Chat ID`) sale enmascarado.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

NOTES_MAX = 6000
USER_MAX = 4000
TOOL_MAX = 1500
NOTE_MAX = 1500

# Encabezados con que `ContextBuilder` arma las instrucciones: los archivos del
# workspace y las secciones de nivel 1. No se parte por el separador `---`: los
# archivos (AGENTS.md) también lo usan adentro.
_PART_RE = re.compile(
    r"^(?:## (?P<file>AGENTS|SOUL|USER|TOOLS|IDENTITY)\.md"
    r"|# (?P<section>Memory|Retrieved Context|Active Skills|Skills))[ \t]*$",
    re.MULTILINE,
)
_NOTES_SECTION = "Retrieved Context"


def _bound(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _text(content: Any) -> str:
    """El texto de un mensaje; una imagen se nombra, no se copia."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    pieces: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            pieces.append(part["text"])
        elif part.get("type") == "image_url":
            pieces.append("[imagen]")
    return "\n".join(pieces)


def _masked(text: str, chat_id: str | None) -> str:
    return text.replace(chat_id, "···" + chat_id[-4:]) if chat_id else text


def _parts(system: str) -> list[tuple[str, int, int]]:
    """`(nombre, inicio, fin)` de cada parte de las instrucciones, en orden."""
    marks = [(m.start(), m.end(), f"{m['file']}.md" if m["file"] else m["section"]) for m in _PART_RE.finditer(system)]
    out: list[tuple[str, int, int]] = []
    first = marks[0][0] if marks else len(system)
    if system[:first].strip():
        heading = system[:first].lstrip().split("\n", 1)[0].lstrip("#").strip()
        out.append((heading or "Instrucciones", 0, first))
    for i, (start, _end, name) in enumerate(marks):
        out.append((name, start, marks[i + 1][0] if i + 1 < len(marks) else len(system)))
    return out


def _notes(system: str) -> str:
    """Las notas del turno: el cuerpo de `# Retrieved Context`."""
    for name, start, end in _parts(system):
        if name == _NOTES_SECTION:
            body = system[start:end].split("\n", 1)[1] if "\n" in system[start:end] else ""
            body = body.strip()
            return body[:-3].rstrip() if body.endswith("---") else body
    return ""


def _new_entry(message: dict[str, Any], chat_id: str | None) -> dict[str, Any] | None:
    role = message.get("role")
    text = _text(message.get("content"))
    if role == "tool":
        return {"role": "tool", "name": message.get("name"), "text": _bound(text, TOOL_MAX)}
    if role == "system":
        return {"role": "system", "text": _bound(text, NOTE_MAX)}
    if role == "user":
        return {"role": "user", "text": _bound(_masked(text, chat_id), USER_MAX)}
    return None  # la respuesta del modelo: ya está en la ronda anterior


def round_input(messages: list[dict[str, Any]], *, since: int, chat_id: str | None = None) -> dict[str, Any]:
    """Lo que recibe el modelo en la ronda que empieza con `messages`, si la
    anterior terminó en `since` (0 = primera ronda)."""
    if since:
        return {"new": [e for m in messages[since:] if (e := _new_entry(m, chat_id)) is not None]}
    system = _text(messages[0].get("content")) if messages and messages[0].get("role") == "system" else ""
    last = messages[-1] if messages and messages[-1].get("role") == "user" else None
    history = messages[1 if system else 0 : -1 if last is not None else None]
    return {
        "system": {"chars": len(system), "parts": [{"name": n, "chars": e - s} for n, s, e in _parts(system)]},
        "notes": _bound(_masked(_notes(system), chat_id), NOTES_MAX),
        "history": dict(Counter(str(m.get("role")) for m in history)),
        "new": [_new_entry(last, chat_id)] if last is not None else [],
    }
