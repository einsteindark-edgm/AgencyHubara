"""Los builtins del paquete `operador` (App Operador).

Lo que el paquete pide por nombre y sigue siendo código: el texto que ve Jev
(la conversación en orden, la etapa de la venta, cuánto lleva esperando el
cliente), las opciones de la burbuja (las jugadas legales que ya calcularon
las reglas de `mobile_rules`), lo que dicen las reglas cuando Jev duda y el
piso (un tema de salud o un pedido que no se pudo registrar siguen graves). Las
preguntas, la certeza que se pide y cómo se arma el valor son el paquete.
PURO: sin I/O.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: Etapa del embudo (`resolve_funnel_stage`) → en palabras.
_STAGES = {
    "etapa_descubrimiento": "el cliente está conociendo los productos",
    "etapa_variantes": "el cliente elige el aroma, el color o el diseño",
    "etapa_datos_envio": "faltan los datos de envío",
    "etapa_cierre": "falta confirmar el pedido y el pago",
    "etapa_postcierre": "el pedido ya está registrado (post-venta)",
}

#: `component_kind` de lo que la tienda envió que no es texto → en palabras.
_COMPONENTS = {
    "products_list": "una lista de productos",
    "product_detail": "la ficha de un producto",
    "product_gallery": "fotos de un producto",
    "variant_picker": "un selector de opciones",
    "shipping_flow": "el formulario de envío",
    "order_confirmation": "el resumen del pedido",
    "payment_instructions": "los datos de pago",
    "shipping_rates": "las tarifas de envío",
    "quick_replies": "botones de respuesta rápida",
}

#: `escalation_reason` → por qué la conversación pasó a una persona.
_REASONS = {
    "EXPLICIT_REQUEST": "el cliente pidió hablar con una persona",
    "HEALTH_SAFETY": "un tema de salud o seguridad",
    "POST_SALE_ISSUE": "un problema con su pedido",
    "SHIPPING_ISSUE": "un problema con el envío",
    "PAYMENT_VERIFICATION_PENDING": "espera que verifiquen su pago",
    "ORDER_REGISTRATION_FAILED": "no se pudo registrar su pedido",
    "CHECKOUT_VERIFY_FAILED": "el bot no pudo verificar su pedido",
}

#: Motivos que siguen graves aunque Jev diga otra cosa (los mismos que las reglas).
SAFETY_REASONS = frozenset({"HEALTH_SAFETY", "ORDER_REGISTRATION_FAILED"})

#: La lectura anterior de un incendio, en palabras (la «evaluación anterior» que ve Jev).
_SEVERITY_WORDS = {"grave": "grave", "hoy": "hoy", "espera": "puede esperar"}
_KIND_WORDS = {
    "wants_human": "pide una persona",
    "angry": "queja o molestia",
    "asking_status": "pregunta por su pedido",
    "payment_proof": "comprobante de pago",
    "order_problem": "problema con el pedido o el envío",
    "health": "tema de salud",
    "praise": "felicita o agradece",
    "sale_at_risk": "quiere comprar",
    "bot_stuck": "el bot no pudo seguir",
    "other": "otra cosa",
}


@dataclass(frozen=True)
class BubbleInput:
    """Un chat con las burbujas que ya armaron las reglas (en su orden)."""

    stage: str
    suggestions: tuple[Mapping[str, Any], ...]
    events: tuple[Mapping[str, Any], ...] = field(default=())


@dataclass(frozen=True)
class FireInput:
    """Un incendio de chat: la tarjeta de las reglas y la conversación."""

    card: Mapping[str, Any]
    reason: str | None
    unanswered_count: int
    wait_ms: int
    events: tuple[Mapping[str, Any], ...] = field(default=())
    #: Lo que Jev leyó la vez anterior («hoy, pregunta por su pedido»), o None.
    previous: str | None = None
    #: El bot pasó la conversación a humano y nadie de la tienda ha respondido (v2: sigue grave).
    handoff: bool = False
    #: Minutos que lleva esperando (v2: una hora o más sigue grave si las reglas dicen grave).
    waited_min: int = 0


def _one_line(text: Any, max_chars: int) -> str:
    return " ".join(str(text or "").split())[:max_chars]


def _lines(events: Sequence[Mapping[str, Any]], *, max_messages: int, max_chars: int) -> list[str]:
    """La conversación que vio el cliente, en orden: lo que escribió, lo que
    le respondieron (el bot o el operador) y lo que se le envió sin texto."""
    out: list[str] = []
    for event in events:
        role = event.get("role")
        if role == "user":
            body = _one_line(event.get("content"), max_chars) or "(un adjunto)"
            out.append(f"[cliente] {body}")
        elif role == "assistant" and event.get("kind") == "ui_component":
            what = _COMPONENTS.get(str(event.get("component_kind")), "un mensaje con botones")
            out.append(f"[tienda] (le envió {what})")
        elif role == "assistant" and event.get("content") and not event.get("tool_calls"):
            who = "operador" if event.get("sender") == "human" else "bot"
            out.append(f"[{who}] {_one_line(event.get('content'), max_chars)}")
    return out[-max_messages:]


def _wait(ms: int) -> str:
    minutes = max(0, ms) // 60_000
    if minutes < 1:
        return "menos de 1 minuto"
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    return f"{hours} h" if hours < 24 else f"{hours // 24} días"


def _key(label: str) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFKD", label.lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", plain).strip("_") or "accion"


def _bubble_text(suggestion: Mapping[str, Any]) -> str:
    """Lo que lee Jev de una burbuja: su texto y el producto, si lo lleva."""
    args = (suggestion.get("action") or {}).get("args") or {}
    handle = args.get("product") or args.get("handle")
    product = f" (producto: {str(handle).replace('-', ' ')})" if handle else ""
    return f"{suggestion.get('label', '')}{product}"


# ── burbuja ──


def chat_board(inp: BubbleInput, *, max_messages: int, max_chars: int) -> str | None:
    """Lo que ve Jev para elegir la burbuja; None si no hay burbujas (no se pregunta)."""
    if not inp.suggestions:
        return None
    lines = [
        "Conversación de WhatsApp de una tienda con un cliente (en orden, lo último al final). Ahora atiende una "
        "persona de la tienda (el operador), que puede enviar con un toque las acciones de abajo.",
        f"En qué va la venta: {_STAGES.get(inp.stage, 'sin etapa clara')}.",
        *_lines(inp.events, max_messages=max_messages, max_chars=max_chars),
        "Acciones que el operador puede enviar ahora: " + "; ".join(_bubble_text(s) for s in inp.suggestions) + ".",
    ]
    return "\n".join(lines)


def legal_bubbles(inp: BubbleInput, *, reserved: Sequence[str] = ()) -> dict[str, tuple[str, str]]:
    """Una opción por burbuja legal: clave corta, etiqueta para Jev y su
    posición en la lista de las reglas (el valor)."""
    taken = set(reserved)
    out: dict[str, tuple[str, str]] = {}
    for index, suggestion in enumerate(inp.suggestions):
        base = _key(str(suggestion.get("label", "")))
        key, n = base, 2
        while key in taken:
            key, n = f"{base}_{n}", n + 1
        taken.add(key)
        out[key] = (_bubble_text(suggestion), str(index))
    return out


def rules_first(inp: BubbleInput) -> str:
    """Lo que dicen las reglas: la primera burbuja ("" si no hay)."""
    return "0" if inp.suggestions else ""


# ── incendio ──


def fire_board(inp: FireInput, *, max_messages: int, max_chars: int) -> str:
    """Lo que ve Jev para clasificar el incendio."""
    facts = f"Lleva {_wait(inp.wait_ms)} sin respuesta; mensajes del cliente sin responder: {inp.unanswered_count}."
    lines = [
        "Conversación de WhatsApp de una tienda con un cliente que espera la respuesta de una persona de la tienda "
        "(en orden, lo último al final).",
        facts,
    ]
    if inp.reason:
        lines.append(f"Por qué pasó a una persona: {_REASONS.get(inp.reason, 'otro motivo')}.")
    if inp.previous:
        lines.append(f"Evaluación anterior de este chat: {inp.previous}.")
    lines += _lines(inp.events, max_messages=max_messages, max_chars=max_chars)
    return "\n".join(lines)


def reading_words(value: Mapping[str, Any]) -> str:
    """Lo que Jev leyó de un incendio, en palabras: la `previous` de la próxima vez."""
    severity = _SEVERITY_WORDS.get(str(value.get("severity")), str(value.get("severity")))
    return f"{severity}, {_KIND_WORDS.get(str(value.get('kind')), 'otra cosa')}"


def rules_fire(inp: FireInput) -> dict[str, Any]:
    """Lo que dicen las reglas: la gravedad, el tipo y si empeora de su tarjeta."""
    card = inp.card
    return {
        "severity": str(card.get("severity", "espera")),
        "kind": str(card.get("kind", "other")),
        "getting_worse": bool(card.get("getting_worse", False)),
    }


def safety_stays_grave(inp: FireInput, _rule: Any, value: Any) -> Any:
    """Piso: un tema de salud o un pedido que no se pudo registrar sigue grave."""
    if inp.reason in SAFETY_REASONS and isinstance(value, Mapping):
        return {**value, "severity": "grave"}
    return value


# ── comunes ──


def jev(_inp: Any, _rule: Any, value: Any) -> Any:
    return value


def same_value(a: Any, b: Any) -> bool:
    return a == b
