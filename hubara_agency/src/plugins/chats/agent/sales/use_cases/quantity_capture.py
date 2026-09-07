"""Captura determinista de la cantidad en respuestas compuestas.

Incidente 2026-09-07 (run 943e6bff, session wa_573229041190): el agente
preguntó "¿Cuántas unidades deseas?" y el cliente respondió "Una que colores
tienes?" — cantidad + pregunta nueva en la misma frase. El LLM atendió solo
la pregunta (mandó el picker de colores) y NUNCA llamó `set_order_slot`
(cantidad); en el run siguiente (a9492a6f) volvió a preguntar la cantidad y
el cliente contestó "Ya te había dicho que una".

`order_draft.py` ya resuelve "no re-preguntar lo que está en el draft", pero
el draft solo se llena si el LLM llama la tool — y en una respuesta compuesta
el modelo se queda con la parte "pregunta" y suelta la parte "dato". Este
módulo cierra ese hueco con la misma filosofía (determinismo PREVENTIVO):
cuando el agente ACABA de preguntar la cantidad y el cliente ARRANCA su
mensaje con una, la fijamos en el draft ANTES de armar el prompt. El LLM lee
"Cantidad: 1" pineado en `[DATOS DEL PEDIDO YA CONFIRMADOS]` en vez de tener
que inferirlo del texto.

Gates (todos deben cumplirse — el parser es deliberadamente CONSERVADOR;
falso negativo = comportamiento de hoy, falso positivo = cantidad equivocada
persistida, que es peor):
  1. La última burbuja VISIBLE del agente pregunta la cantidad.
  2. Hay pedido en curso: episodio activo, sin `order_id`, con `producto`.
  3. El draft no tiene cantidad, o la tiene al MISMO valor (idempotente ante
     retry de la activity). Cantidad distinta ya fijada → no se pisa a ciegas.
  4. El mensaje ARRANCA con una cantidad (dígitos o palabra 1..20) y la
     palabra siguiente no es un uso no-numérico de "una/un" ("una pregunta",
     "un momento", "una más", "un par").

DEHA: funciones puras sobre `history` (list[dict]) y `metadata` (dict que se
MUTA por argumento, igual que `update_order_draft`). Sin I/O, sin reloj —
la activity `build_prompt` de Sales lee/persiste.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    get_active_episode,
)
from src.plugins.chats.agent.sales.use_cases.order_draft import (
    update_order_draft,
)

_WORD_NUMBERS: dict[str, int] = {
    "un": 1, "una": 1, "uno": 1,
    "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
    "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13,
    "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20,
}

# Prefijos que NO cambian el sentido ("solo una", "solamente 2").
_LEADING_FILLERS: frozenset[str] = frozenset({"solo", "solamente", "nada", "mas"})

# Palabra siguiente que convierte "una/un/dos..." en algo que NO es cantidad
# de producto. Normalizadas sin acentos.
_NON_QUANTITY_FOLLOWERS: frozenset[str] = frozenset({
    "pregunta", "preguntas", "preguntica", "preguntita", "duda", "dudas",
    "consulta", "consultas", "inquietud", "cosa", "cosita", "momento",
    "momentico", "segundo", "favor", "vez", "veces", "mas", "otra", "otro",
    "otras", "otros", "amiga", "amigo", "amigas", "amigos", "persona",
    "senora", "senor", "docena", "docenas", "par", "pares", "caja", "cajas",
    "mitad", "cada", "hora", "horas", "dia", "dias", "semana", "semanas",
    "mes", "meses", "sugerencia", "recomendacion", "opcion", "opciones",
    "idea", "foto", "fotos", "imagen", "video",
})

_QUANTITY_QUESTION_RE = re.compile(
    r"(cuant[ao]s?\s+(unidades|velas|velones|piezas|quieres|quisieras|deseas|"
    r"necesitas|te\s+gustar|vas\s+a|serian|son)|que\s+cantidad|"
    r"cantidad\s+(deseas|quieres|necesitas|te\s+gustar))"
)


def _normalize(text: str) -> str:
    """Minúsculas, sin acentos (NFKD) — comparaciones estables."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def agent_asked_quantity(text: str | None) -> bool:
    """¿La burbuja del agente pregunta la cantidad? Regex tolerante sobre el
    texto normalizado (cubre la fórmula del guion "¿Cuántas unidades deseas?"
    y variantes naturales del modelo)."""
    if not text:
        return False
    return _QUANTITY_QUESTION_RE.search(_normalize(text)) is not None


def last_visible_agent_text(history: list[dict[str, Any]]) -> str | None:
    """Última burbuja del agente que el cliente VIO: assistant con contenido
    y sin `tool_calls` (la narración pre-tool no se envía — PR #213)."""
    for msg in reversed(history):
        if msg.get("role") != "assistant":
            continue
        if msg.get("tool_calls"):
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return None


def parse_leading_quantity(text: str | None) -> int | None:
    """Cantidad con la que ARRANCA el mensaje del cliente, o None.

    Acepta dígitos ("2", "12") o palabras ("una", "dos"), con rellenos
    opcionales delante ("solo una") y emojis/puntuación inicial. Rechaza
    mensajes de sistema/botón, cero, y "una/un" seguidos de una palabra que
    los vuelve artículo ("una pregunta", "un momento").
    """
    if not text:
        return None
    raw = text.strip()
    if raw.startswith("["):
        return None  # [SISTEMA] / [el cliente tocó el botón] / [datos de envío]
    norm = _normalize(raw)
    # Quitamos todo lo que no sea letra/dígito/espacio (emojis, ¿, ¡, comas).
    tokens = re.sub(r"[^a-z0-9\s]", " ", norm).split()
    i = 0
    while i < len(tokens) and tokens[i] in _LEADING_FILLERS:
        i += 1
    if i >= len(tokens):
        return None
    head = tokens[i]
    if head.isdigit():
        value = int(head)
    else:
        value = _WORD_NUMBERS.get(head, 0)
    if value <= 0 or value > 999:
        return None
    follower = tokens[i + 1] if i + 1 < len(tokens) else None
    if follower in _NON_QUANTITY_FOLLOWERS:
        return None
    return value


def capture_quantity_from_reply(
    metadata: dict[str, Any],
    *,
    last_agent_text: str | None,
    inbound_text: str | None,
    now_ms: int,
) -> int | None:
    """Fija `cantidad` en el order_draft si todos los gates se cumplen.

    Mutates `metadata` (solo cuando captura). Devuelve la cantidad capturada
    (también cuando ya estaba fijada al mismo valor — idempotente) o None.
    """
    if not agent_asked_quantity(last_agent_text):
        return None
    quantity = parse_leading_quantity(inbound_text)
    if quantity is None:
        return None

    episode = get_active_episode(metadata)
    if episode is None or episode.get("order_id"):
        return None
    draft = episode.get("order_draft")
    slots = draft.get("slots") if isinstance(draft, dict) else None
    if not isinstance(slots, dict) or not slots.get("producto"):
        return None

    existing = str(slots.get("cantidad") or "").strip()
    if existing == str(quantity):
        return quantity
    if existing:
        return None

    update_order_draft(metadata, slots={"cantidad": str(quantity)}, now_ms=now_ms)
    return quantity
