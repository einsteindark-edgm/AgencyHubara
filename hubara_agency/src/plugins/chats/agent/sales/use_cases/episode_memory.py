"""Lo que el LLM sabe del episodio anterior cuando empieza uno nuevo — PURO.

Run 28a8e407 (2026-09-23): el historial del LLM se corta al abrir el episodio
(`llm_history_reset`, lo aplica el worker) y lo anterior viajaba como el
`motivo` que el LLM escribió al cerrar — que podía estar mal (la Trilogía con
un cupón que no era) — pegado por exoclaw delante de CADA mensaje del cliente.

Ahora es UNA línea armada con hechos del episodio cerrado (cómo cerró, qué
pedido, qué productos) que va al inicio del primer mensaje del episodio
nuevo: queda grabada una sola vez, justo donde empieza el historial limpio.
Tuteo colombiano (REGLA #1, guard test_no_voseo_in_agent_strings.py).
"""
from __future__ import annotations

from typing import Any

from src.plugins.chats.shared.draft_items import draft_items

#: Cómo terminó el episodio, por tag de cierre (`episode_lifecycle`).
_OUTCOMES: dict[str, str] = {
    "COMPRA_EXITOSA": "terminó en una compra{order}",
    "CONFIRMADO_PAGO_PENDIENTE": (
        "terminó con un pedido registrado{order} que el equipo está "
        "gestionando (verificación del pago y envío)"
    ),
    "CONFIRMADO_SIN_DATOS": (
        "el cliente confirmó una compra{order} pero faltaron datos de envío; "
        "el equipo la está gestionando"
    ),
    "RECHAZO": "el cliente decidió no comprar",
    "TIMEOUT": "quedó sin respuesta del cliente por mucho tiempo",
    "CAMPAIGN_REPLY": "quedó abierta, sin compra",
}


def _item_label(item: dict[str, Any]) -> str:
    producto = str(item.get("producto") or "").strip()
    if not producto:
        return ""
    cantidad = str(item.get("cantidad") or "").strip()
    variants = [
        str(item[key]).strip()
        for key in ("aroma", "color", "diseno")
        if str(item.get(key) or "").strip()
    ]
    label = f"{cantidad}× {producto}" if cantidad else producto
    return f"{label} ({', '.join(variants)})" if variants else label


def previous_episode_summary(episode: dict[str, Any]) -> str:
    """Una línea determinista: cómo cerró el episodio y de qué se habló."""
    order_id = episode.get("order_id")
    order = f" ({order_id})" if isinstance(order_id, str) and order_id else ""
    template = _OUTCOMES.get(str(episode.get("closing_tag") or ""), "se cerró")
    summary = template.format(order=order)
    items = [
        label for label in map(_item_label, draft_items(episode.get("order_draft")))
        if label
    ]
    if items:
        summary += "; hablaron de " + ", ".join(items)
    coupon = episode.get("applied_coupon")
    code = coupon.get("code") if isinstance(coupon, dict) else None
    if isinstance(code, str) and code.strip():
        summary += f", con el cupón {code.strip()}"
    return summary


def with_previous_episode(episode: dict[str, Any], text: str) -> str:
    """El primer mensaje del episodio nuevo con lo anterior adelante."""
    return (
        "[Conversación anterior con este cliente, ya cerrada: "
        f"{previous_episode_summary(episode)}. Esta es una conversación nueva: "
        "no la retomes salvo que el cliente la mencione.]\n"
        f"{text}"
    )


def request_clean_llm_history(episode: dict[str, Any]) -> None:
    """Pide cortar el historial del LLM de cada agente al empezar este
    episodio (mutación in-place). `applied` lo llena el worker de cada agente
    al cortar (`src/platform/llm_history_reset.py`) — idempotente por agente.
    """
    episode["llm_history_reset"] = {"applied": []}


def unseen_template_text(events: list[dict[str, Any]]) -> str | None:
    """La plantilla a la que responde el cliente, si es lo último que recibió.

    Las plantillas (remarketing, campañas, avisos) se envían por fuera del
    historial del LLM: solo quedan en el JSONL del dashboard (`kind:
    template`). Hay que llamarla ANTES de persistir el mensaje actual: si lo
    último del log ya es otro mensaje del cliente, esa plantilla ya se citó.
    """
    if not events:
        return None
    last = events[-1]
    if not isinstance(last, dict) or last.get("role") != "assistant":
        return None
    if last.get("kind") != "template":
        return None
    content = last.get("content")
    return content.strip() if isinstance(content, str) and content.strip() else None


def quote_template_in_turn(template_text: str, text: str) -> str:
    """El mensaje del cliente con la plantilla que recibió citada adelante."""
    return (
        f"[El cliente responde a este mensaje que le enviamos: «{template_text}»]\n"
        f"{text}"
    )


#: Cuántas líneas de lo que pasó sin el bot viajan en el turno, y su largo.
TEAM_EXCHANGE_MAX_LINES = 8
_TEAM_LINE_MAX_CHARS = 300

_TEAM_SPEAKERS = {"colega": "Tu colega", "cliente": "Cliente", "aviso": "Mensaje automático"}


def _team_speaker(event: dict[str, Any]) -> str | None:
    """Quién escribió un mensaje que el LLM no vio; ``None`` = lo escribió el
    bot (el LLM ya lo tiene en su historial)."""
    if event.get("role") == "user":
        return "cliente"
    if event.get("role") != "assistant":
        return None
    if event.get("sender") == "human":
        return "colega"
    # Una plantilla (aviso del ETA, gancho de remarketing) sale por fuera del
    # historial del LLM; cualquier otro mensaje sin `sender` es del bot.
    return "aviso" if event.get("kind") == "template" else None


def _team_text(event: dict[str, Any]) -> str:
    content = event.get("content")
    text = " ".join(content.split()) if isinstance(content, str) else ""
    if not text and event.get("image_url"):
        text = "(una foto)"
    elif not text and event.get("document_url"):
        text = "(un documento)"
    if len(text) > _TEAM_LINE_MAX_CHARS:
        text = text[: _TEAM_LINE_MAX_CHARS - 1].rstrip() + "…"
    return text


def unseen_team_exchange(events: list[dict[str, Any]]) -> list[tuple[str, str]] | None:
    """Lo que pasó en el chat después del último mensaje del bot, si un colega
    del equipo escribió algo: ``[(quién, texto)]`` en orden, o ``None``.

    Los mensajes del equipo (`sender: human`) van solo al JSONL del dashboard:
    el historial del LLM no los tiene, ni lo que el cliente le contestó al
    colega mientras tenía el chat. Hay que llamarla ANTES de persistir el
    mensaje actual (como `unseen_template_text`).
    """
    tail: list[dict[str, Any]] = []
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        if event.get("role") == "assistant" and _team_speaker(event) is None:
            break  # el último mensaje del bot: de ahí para atrás ya lo vio
        tail.append(event)
    tail.reverse()
    if not any(_team_speaker(event) == "colega" for event in tail):
        return None
    lines = [
        (speaker, text)
        for event in tail
        if (speaker := _team_speaker(event)) is not None and (text := _team_text(event))
    ]
    return lines[-TEAM_EXCHANGE_MAX_LINES:] or None


_TEAM_NOTE_HEAD = "[Después de tu último mensaje, un colega del equipo"
#: Las líneas de la nota que dijimos nosotros (el colega o un aviso).
_OUR_LINE_PREFIXES = tuple(f"- {_TEAM_SPEAKERS[who]}: «" for who in ("colega", "aviso"))


def quote_team_exchange_in_turn(exchange: list[tuple[str, str]], text: str) -> str:
    """El mensaje del cliente con lo que escribió el colega citado adelante.

    Sin corchetes adentro: la nota va entre corchetes y la calificación quita
    las notas del ingest hasta el primer «]» (`_INGEST_NOTE_RE`)."""
    lines = "\n".join(
        f"- {_TEAM_SPEAKERS.get(who, who)}: «{said.replace('[', '(').replace(']', ')')}»"
        for who, said in exchange
    )
    return (
        f"{_TEAM_NOTE_HEAD} le escribió al cliente "
        "en este chat (el cliente lo ve como la misma conversación):\n"
        f"{lines}\n"
        "Lo que el cliente escribe ahora puede ser la respuesta a tu colega: sigue "
        "desde ahí y no contradigas lo que le dijo.]\n"
        f"{text}"
    )


def our_lines_in_turn(text: str) -> list[str]:
    """Lo que dijimos nosotros (el colega o un aviso) según la nota que puso
    `quote_team_exchange_in_turn` en el turno; ``[]`` si el turno no la trae.
    La lee la calificación, como la cita de una plantilla."""
    start = text.find(_TEAM_NOTE_HEAD)
    if start < 0:
        return []
    end = text.find("]", start)
    note = text[start : end if end >= 0 else len(text)]
    return [
        line[len(prefix) : -1]
        for line in note.splitlines()
        for prefix in _OUR_LINE_PREFIXES
        if line.startswith(prefix) and line.endswith("»")
    ]
