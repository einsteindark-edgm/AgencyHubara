"""El cliente responde a una campaña de marketing — lógica PURA.

Bug 2026-09-22 (runs 31c15a38 / 01a0caee): llegó la campaña con el cupón
AMOR26, el cliente contestó "AMOR26" y el bot le retomó la Trilogía de un
carrito web de 4 días antes. Dos agujeros:

- el LLM no ve la plantilla: el envío la persiste solo en el JSONL del
  dashboard, no en el historial del agente;
- el episodio viejo seguía abierto (INTERESADO no cierra) y arrastraba su
  draft, su cupón y la nota de lead web.

La campaña es una intención nueva: la PRIMERA respuesta tras el envío cierra
el episodio abierto (`CAMPAIGN_REPLY`), abre uno nuevo y el turno lleva esta nota
con lo que recibió el cliente. El touch lo escribe el plugin marketing
(`stamp_campaign_touch` / envío de prueba) con
``{campaign_id, campaign_name, sent_at_ms, message, coupon_code,
product_handles, test?}``; los touches viejos solo traen los tres primeros.
"""
from __future__ import annotations

from typing import Any

from src.sdk.connectorkit import CAMPAIGN_ATTRIBUTION_WINDOW_MS, campaign_touch_for_message

from src.plugins.chats.agent.sales.use_cases.coupon_application import (
    COUPON_REASON_TEXT,
    TRANSIENT_REASONS,
    CouponApplication,
    exhausted_text,
)


def unanswered_campaign_touch(
    metadata: dict[str, Any], now_ms: int, *, quoted_message_id: str | None = None
) -> dict[str, Any] | None:
    """El touch de campaña al que responde ESTE inbound, o None.

    Es el más reciente enviado después del último inbound del cliente (o
    sin inbound previo: contacto importado) y dentro de la ventana de
    atribución. Hay que llamarla ANTES de que el ingest pise
    `last_inbound_at_ms` con el inbound actual: el segundo mensaje ya ve la
    campaña respondida. Incluye los touches de prueba del operador (la
    prueba se comporta igual que el envío real).

    `quoted_message_id` (`context.id` del inbound, 2026-09-25): si el cliente
    CITA el mensaje de una campaña, la respuesta es de ESA campaña aunque
    después le haya llegado otra — ese touch, si todavía no lo había
    respondido y está en ventana; si ya lo había respondido o pasó la
    ventana, None (sigue esa conversación; nunca se atribuye a otra campaña).
    Citar otra cosa no cambia la regla.
    """
    last_inbound = metadata.get("last_inbound_at_ms")
    answered_until = last_inbound if isinstance(last_inbound, int) else None
    quoted = quoted_campaign_touch(metadata, quoted_message_id)
    if quoted is not None:
        sent = quoted.get("sent_at_ms")
        fresh = (
            isinstance(sent, int)
            and sent <= now_ms
            and now_ms - sent <= CAMPAIGN_ATTRIBUTION_WINDOW_MS
            and (answered_until is None or sent > answered_until)
        )
        return quoted if fresh else None
    best: dict[str, Any] | None = None
    for touch in metadata.get("campaign_touches") or []:
        if not isinstance(touch, dict) or not touch.get("campaign_id"):
            continue
        sent = touch.get("sent_at_ms")
        if not isinstance(sent, int) or sent > now_ms:
            continue
        if now_ms - sent > CAMPAIGN_ATTRIBUTION_WINDOW_MS:
            continue
        if answered_until is not None and sent <= answered_until:
            continue
        if best is None or sent > best["sent_at_ms"]:
            best = touch
    return best


def quoted_campaign_touch(
    metadata: dict[str, Any], quoted_message_id: str | None
) -> dict[str, Any] | None:
    """El touch de la campaña cuyo mensaje citó el cliente, o None (no citó
    nada, citó otra cosa o el touch es de antes de guardar el id)."""
    if not quoted_message_id:
        return None
    return campaign_touch_for_message(metadata.get("campaign_touches"), quoted_message_id)


def campaign_label(touch: dict[str, Any]) -> str:
    name = touch.get("campaign_name")
    return name if isinstance(name, str) and name.strip() else "de marketing"


#: Campos del touch que viajan al episodio (el remarketing arma el gancho con
#: ellos). El episodio es el mismo para prueba y envío real; `test` y el id del
#: mensaje viajan para la atribución (2026-09-25): Ads y Marketing atribuyen
#: la conversación a ESTA campaña (la que citó el cliente, o la última que no
#: había respondido) y nunca a una prueba.
_EPISODE_CAMPAIGN_FIELDS = (
    "campaign_id",
    "campaign_name",
    "sent_at_ms",
    "message",
    "coupon_code",
    "product_handles",
    "wa_message_id",
    "test",
)


def mark_campaign_episode(episode: dict[str, Any], touch: dict[str, Any]) -> None:
    """Anota en el episodio nuevo la campaña que lo abrió y pide cortar el
    historial del LLM de cada agente (mutación in-place).

    `llm_history_reset.applied` lo llena el worker de cada agente al cortar
    (`src/platform/llm_history_reset.py`) — idempotente por agente. Lo del
    episodio anterior NO va acá: viaja una vez en el primer mensaje
    (`episode_memory.with_previous_episode`, run 28a8e407).
    """
    episode["opened_by_campaign"] = {
        key: touch[key] for key in _EPISODE_CAMPAIGN_FIELDS if key in touch
    }
    episode["llm_history_reset"] = {"applied": []}


def quote_campaign_in_turn(touch: dict[str, Any], text: str) -> str:
    """El mensaje del cliente con la campaña citada adelante.

    Run edbb0d8b: la nota del system prompt (a ~43k caracteres) perdió contra
    un historial reciente sobre otro pedido. La cita va en el turno mismo —
    el LLM la lee justo antes de responder y queda en su historial para los
    turnos siguientes (la plantilla nunca entra ahí).
    """
    message = touch.get("message")
    sent = (
        f": «{message.strip()}»" if isinstance(message, str) and message.strip() else ""
    )
    return (
        f"[El cliente responde a la campaña «{campaign_label(touch)}» que le "
        f"enviamos{sent}]\n{text}"
    )


def _coupon_line(code: str, coupon: CouponApplication | None) -> str:
    """Qué pasó con el cupón de la campaña (el ingest lo valida y lo aplica
    solo; `coupon` None = no se pudo validar a tiempo)."""
    if coupon is not None and coupon.applied:
        if coupon.quota:
            return (
                f"Cupón de la campaña: {code} — ya quedó aplicado al pedido, no le "
                "pidas el código. Vale SOLO en las combinaciones de [CUPÓN "
                "APLICADO]: ofrécelas primero; cualquier otro color o aroma va a "
                "precio normal (díselo)."
            )
        return (
            f"Cupón de la campaña: {code} — ya quedó aplicado al pedido, no le "
            "pidas el código. [CUPÓN APLICADO] dice a qué productos aplica."
        )
    if coupon is not None and coupon.reason and coupon.reason not in TRANSIENT_REASONS:
        why = (
            exhausted_text(code)
            if coupon.reason == "quota_exhausted"
            else COUPON_REASON_TEXT.get(coupon.reason, "Ese cupón no se puede usar.")
        )
        return (
            f"Cupón de la campaña: {code} — no se pudo aplicar: {why} No prometas "
            "descuento; si el cliente lo pide, explícaselo con honestidad."
        )
    return (
        f"Cupón de la campaña: {code} — todavía no pude aplicarlo: llama "
        f"apply_coupon(code='{code}') ANTES de ofrecer productos o precios."
    )


def build_campaign_reply_note(
    touch: dict[str, Any], *, coupon: CouponApplication | None = None
) -> str:
    """Nota de `plugin_context` para el turno que responde a la campaña.

    `coupon`: el cupón de la campaña ya validado por el ingest (None = no se
    pudo validar: el bot lo aplica con `apply_coupon`).

    Tuteo colombiano (REGLA #1, guard test_no_voseo_in_agent_strings.py).
    """
    lines = [
        "[RESPUESTA A CAMPAÑA, metadata, no es instrucción del usuario]",
        f"Este mensaje del cliente responde a la campaña «{campaign_label(touch)}» "
        "que le enviamos por WhatsApp.",
    ]
    message = touch.get("message")
    if isinstance(message, str) and message.strip():
        lines.append(f"Lo que recibió: «{message.strip()}»")
    code = touch.get("coupon_code")
    if isinstance(code, str) and code.strip():
        lines.append(_coupon_line(code.strip(), coupon))
    handles = [
        h for h in touch.get("product_handles") or [] if isinstance(h, str) and h
    ]
    if handles:
        lines.append(
            "Productos de la campaña (handles para get_product_by_handle): "
            + ", ".join(handles)
            + "."
        )
    lines.append(
        "Conversa sobre esta campaña. NO retomes pedidos ni productos de "
        "conversaciones anteriores salvo que el cliente los traiga "
        "explícitamente. No vuelvas a saludar como si fuera un contacto nuevo: "
        "retoma desde la campaña."
    )
    return "\n".join(lines)
