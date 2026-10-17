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

from collections.abc import Collection
from datetime import datetime
from typing import Any

from src.plugins.chats.agent.sales.contracts import HandoffReply
from src.plugins.chats.shared.draft_items import draft_items
from src.plugins.chats.shared.team_exchange import (
    quote_team_exchange_in_turn as quote_team_exchange_in_turn,
)
from src.plugins.chats.shared.team_exchange import quote_unseen_in_turn

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


def _team_speaker(event: dict[str, Any]) -> str | None:
    """Quién escribió un mensaje que el LLM no vio; ``None`` = lo escribió el
    bot (el LLM ya lo tiene en su historial)."""
    if event.get("role") == "user":
        return "cliente"
    if event.get("role") != "assistant":
        return None
    if event.get("sender") == "human":
        return "colega"
    # Una plantilla (aviso del ETA, gancho de remarketing) o un aviso (la
    # invitación a calificar, el gracias con el cupón — premortem C5) sale por
    # fuera del historial del LLM, y también el gancho de texto libre de
    # remarketing (`agent`, caso 2026-10-16: sin contarlo, la nota se cortaba
    # en él). Cualquier otro mensaje sin `sender` es del bot.
    if event.get("kind") in ("template", NOTICE_KIND) or event.get("agent") == "remarketing":
        return "aviso"
    return None


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
    mensaje actual (como `unseen_template_text`). La nota la arma
    `quote_team_exchange_in_turn` (`chats/shared/team_exchange.py`).
    """
    tail = _unseen_tail(events)
    if not any(_team_speaker(event) == "colega" for event in tail):
        return None
    return _exchange_lines(tail) or None


def _unseen_tail(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Los eventos después del último mensaje del bot de ventas, en orden."""
    tail: list[dict[str, Any]] = []
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        if event.get("role") == "assistant" and _team_speaker(event) is None:
            break  # el último mensaje del bot: de ahí para atrás ya lo vio
        tail.append(event)
    tail.reverse()
    return tail


def _exchange_lines(tail: list[dict[str, Any]]) -> list[tuple[str, str]]:
    lines = [
        (speaker, text)
        for event in tail
        if (speaker := _team_speaker(event)) is not None and (text := _team_text(event))
    ]
    return lines[-TEAM_EXCHANGE_MAX_LINES:]


def _said(event: dict[str, Any]) -> str:
    """Lo que escribió el cliente, tal cual (sin recortar ni aplanar: después
    del traspaso suelen venir los datos de envío). Una foto o un documento sin
    texto, como en la nota."""
    content = event.get("content")
    text = content.strip() if isinstance(content, str) else ""
    return text or _team_text(event)


def _event_ms(event: dict[str, Any]) -> int | None:
    stamp = event.get("timestamp")
    if not isinstance(stamp, str):
        return None
    try:
        return int(datetime.fromisoformat(stamp).timestamp() * 1000)
    except ValueError:
        return None


def handoff_reply_of(
    events: list[dict[str, Any]], *, already_pending: Collection[str] = ()
) -> HandoffReply | None:
    """El turno de Ventas que sigue al traspaso de remarketing (caso
    2026-10-16), o ``None`` si el cliente no escribió después de lo último.

    Lo que el cliente escribió al final del chat (sus mensajes después del
    último que no es suyo), tal cual, con la nota de lo que pasó después del
    último mensaje de Ventas: lo que escribió un colega, lo que le contestó el
    cliente, los avisos y el gancho de remarketing. Ventas no tiene nada de
    eso en su historial; antes solo recibía el resumen de remarketing.

    `already_pending`: wamids de mensajes del cliente que ya le llegaron a
    Ventas por señal (traen su foto o su nota del ingest): no se repiten acá.
    """
    tail = _unseen_tail(events)
    split = len(tail)
    while split and tail[split - 1].get("role") == "user":
        split -= 1
    pending = set(already_pending)
    replies = [
        event
        for event in tail[split:]
        if _said(event) and str(event.get("wamid") or "") not in pending
    ]
    if not replies:
        return None
    said = "\n".join(_said(event) for event in replies)
    lines = _exchange_lines(tail[:split])
    if not lines:
        text = said
    elif any(who == "colega" for who, _ in lines):
        text = quote_team_exchange_in_turn(lines, said)
    else:
        text = quote_unseen_in_turn(lines, said)
    return HandoffReply(
        text=text,
        customer_text=said,
        wamids=[str(event["wamid"]) for event in replies if event.get("wamid")],
        ts_ms=_event_ms(replies[-1]),
    )


#: Avisos que salen por fuera del bot (invitación a calificar, el gracias con
#: el cupón): `kind: notice` en el JSONL del chat (`whatsapp.activities`).
NOTICE_KIND = "notice"
#: Cuántos avisos viajan en el turno, y su largo.
_NOTICES_MAX = 3


def unseen_notices(events: list[dict[str, Any]]) -> list[str] | None:
    """Los avisos (`kind: notice`) que el cliente recibió después del último
    mensaje del bot, en orden, o ``None``.

    Premortem C5 (reseñas tras la entrega): la invitación a calificar y el
    gracias con el cupón no entran al historial del LLM; sin esto el bot no
    sabía que le dimos un cupón. Hay que llamarla ANTES de persistir el
    mensaje actual (como `unseen_template_text`). No cuenta para el acuse: un
    «¡Gracias! 🙏» al gracias no es respuesta a una plantilla."""
    found: list[str] = []
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        if event.get("role") == "assistant" and _team_speaker(event) is None:
            break  # el último mensaje del bot: de ahí para atrás ya lo vio
        if event.get("role") == "assistant" and event.get("kind") == NOTICE_KIND:
            text = _team_text(event)
            if text:
                found.append(text)
    found.reverse()
    return found[-_NOTICES_MAX:] or None


def quote_notices_in_turn(notices: list[str], text: str) -> str:
    """El mensaje del cliente con los avisos que recibió citados adelante.
    Sin corchetes adentro (la calificación quita las notas del ingest hasta
    el primer «]»)."""
    quoted = "; ".join(
        f"«{notice.replace('[', '(').replace(']', ')')}»" for notice in notices
    )
    return (
        "[Antes de este mensaje el cliente recibió estos mensajes automáticos "
        f"de la tienda (no los escribiste tú): {quoted}. Puede estar respondiendo "
        "a eso; no lo contradigas.]\n"
        f"{text}"
    )
