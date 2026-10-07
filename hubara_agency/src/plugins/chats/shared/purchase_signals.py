"""Señales deterministas del cliente sobre la compra (2026-09-14).

Incidente (runs 01a0a0eb remarketing / 01a0a0f1 sales): el cliente respondió
al gancho con "Voy apenas en camino a casa" — un APLAZAMIENTO — y el sistema lo
trató como confirmación: remarketing resumió "siguiente paso: datos de envío",
ventas mandó el formulario, el ghosting cerró CONFIRMADO_SIN_DATOS y escaló a
humano. El cliente nunca dijo que sí.

Este módulo es la única fuente de verdad de dos hechos que las guardas leen:

* **Confirmación de compra** (`has_purchase_confirmation`): el cliente dijo que
  sí (texto afirmativo o botón "Confirmar") con un producto ya elegido en el
  draft del episodio activo, o hay una orden registrada. Se persiste en
  `episode.order_draft.confirmed_at_ms` (episodio-scoped: no hay leak a un
  episodio nuevo).
* **Aplazamiento vigente** (`is_current_inbound_deferral`): el ÚLTIMO inbound
  fue un "después". Mientras siga siendo el último, las tools outbound de
  cierre (formulario de envío) se rechazan y el prompt pide texto breve.

Reglas: texto normalizado (minúsculas, sin acentos); el aplazamiento gana sobre
la afirmación ("sí pero luego"); una negación ("no") anula la afirmación.
Funciones puras salvo `register_inbound_purchase_signals`, que muta el dict
de metadata que el ingest ya persiste.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from src.plugins.chats.shared.funnel import active_episode

SIGNAL_KEY = "last_inbound_signal"

_DEFERRAL_PATTERNS = [
    re.compile(p)
    for p in (
        r"\ben camino\b",
        r"\bluego\b",
        r"\bmas tarde",
        r"\bdespues\b",
        r"\bahora no\b",
        r"\bahorita no\b",
        r"\ben un rato\b",
        r"\bcuando llegue\b",
        r"\bdejame\b",
        r"\bdeja(me)? (que )?(miro|reviso|pienso|veo)\b",
        r"\blo pienso\b",
        # "te / les / le": al negocio se le habla en plural ("les escribo la
        # otra semana" — incidente runs 337efe8c / ee3cec91).
        r"\b(te|les|le) aviso\b",
        r"\b(te|les|le) escribo\b",
        r"\b(te|les|le) confirmo\b",
        r"\bmanana\b",
        r"\bestoy ocupad",
        r"\bme ocup[eo]\b",
        r"\bno puedo ahora\b",
        r"\ben la noche\b",
        r"\bmas tardecito\b",
    )
]

_AFFIRMATION_START = re.compile(
    r"^\W*(si|dale|listo|de una|hagale|claro|perfecto|vale|ok|okay|va|bueno|confirmo|confirmado)\b"
)
_AFFIRMATION_ANY = re.compile(
    r"\b(lo quiero|la quiero|me lo llevo|me la llevo|confirmo|confirmado|lo compro|la compro|"
    r"quiero (ese|esa|este|esta|el|la|uno|una|\d+)|dame (\d+|uno|una|ese|esa)|dejalo asi|dejala asi|"
    r"dejalo en|dejala en|asi esta bien|esta bien asi|ese mismo|esa misma|me gusta ese|me gusta esa|"
    r"envia(me)?lo|mandalo|mandamelo)\b"
)
_NEGATION = re.compile(r"^\W*no\b|\bno (quiero|gracias|me interesa|lo quiero)\b")

_CONFIRM_BUTTON_IDS = ("order.confirm", "confirm", "checkout")


def _normalize(text: str) -> str:
    stripped = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in stripped if not unicodedata.combining(c)).lower().strip()


def detect_deferral(text: str | None) -> bool:
    """True si el cliente está aplazando ("luego", "voy en camino", "mañana")."""
    if not text:
        return False
    norm = _normalize(text)
    return any(p.search(norm) for p in _DEFERRAL_PATTERNS)


def detect_purchase_affirmation(text: str | None) -> bool:
    """True si el texto es un sí de compra ("sí", "dale", "lo quiero", "dame 2").

    No decide por sí solo que hay compra: `register_inbound_purchase_signals`
    exige además un producto en el draft. Un "no" al inicio anula.
    """
    if not text:
        return False
    norm = _normalize(text)
    if _NEGATION.search(norm):
        return False
    if "?" in norm or "¿" in norm:
        # Una pregunta ("Si tienes 2 de esa ?", prueba en vivo 2026-09-24)
        # no es un sí: solo cuenta una frase de compra explícita.
        return bool(_AFFIRMATION_ANY.search(norm))
    return bool(_AFFIRMATION_START.search(norm) or _AFFIRMATION_ANY.search(norm))


def _is_confirm_button(interactive: dict[str, Any] | None) -> bool:
    if not isinstance(interactive, dict):
        return False
    if interactive.get("type") not in (None, "button_reply"):
        return False
    button_id = str(interactive.get("id") or "").lower()
    title = _normalize(str(interactive.get("title") or ""))
    return any(button_id.startswith(p) for p in _CONFIRM_BUTTON_IDS) or "confirmar" in title


def _is_cart(order: dict[str, Any] | None) -> bool:
    """Carrito del catálogo de WhatsApp (`type: "order"`) con al menos un ítem."""
    return isinstance(order, dict) and bool(order.get("product_items"))


def classify_inbound_purchase_signal(
    text: str | None,
    *,
    interactive: dict[str, Any] | None = None,
    order: dict[str, Any] | None = None,
) -> tuple[str | None, str]:
    """LEE el inbound (sin escribir): `(tipo, fuente)`.

    `tipo` ∈ ``"affirmation"`` / ``"deferral"`` / ``None``; `fuente` ∈
    ``"cart"`` / ``"button"`` / ``"text"``. Es la regla de hoy de la capacidad
    «compra» del motor de decisiones: el motor puede poner a Jev en esta
    lectura sin tocar la escritura (`apply_inbound_purchase_signal`).

    El carrito del catálogo (run 01a0cb16) y el botón "Confirmar" son
    estructurales: no dependen del texto.
    """
    if _is_cart(order):
        return "affirmation", "cart"
    if _is_confirm_button(interactive):
        return "affirmation", "button"
    if detect_deferral(text):
        return "deferral", "text"
    if detect_purchase_affirmation(text):
        return "affirmation", "text"
    return None, "text"


def apply_inbound_purchase_signal(
    metadata: dict[str, Any],
    kind: str | None,
    confirmed_by: str,
    *,
    now_ms: int,
    message_id: str | None,
    text: str | None,
) -> str | None:
    """ESCRIBE la señal ya leída en `metadata` (mutación) y la devuelve.

    Una afirmación con producto ya elegido en el draft del episodio activo
    marca la confirmación de compra (`order_draft.confirmed_at_ms` +
    `confirmed_by`). El carrito confirma SIEMPRE: nombra producto y cantidad
    por sí mismo, no depende de que el draft ya tenga `producto`.
    """
    if kind is None:
        metadata.pop(SIGNAL_KEY, None)
        return None

    metadata[SIGNAL_KEY] = {
        "kind": kind,
        "at_ms": now_ms,
        "message_id": message_id,
        "text": (text or "")[:120],
    }
    if kind == "affirmation":
        episode = active_episode(metadata)
        if episode and confirmed_by == "cart":
            cart_draft = episode.setdefault("order_draft", {})
            cart_draft["confirmed_at_ms"] = now_ms
            cart_draft["confirmed_by"] = confirmed_by
            return kind
        draft = (episode or {}).get("order_draft") if episode else None
        slots = draft.get("slots") if isinstance(draft, dict) else None
        if isinstance(slots, dict) and str(slots.get("producto") or "").strip():
            draft["confirmed_at_ms"] = now_ms
            draft["confirmed_by"] = confirmed_by
    return kind


def register_inbound_purchase_signals(
    metadata: dict[str, Any],
    text: str | None,
    *,
    now_ms: int,
    message_id: str | None,
    interactive: dict[str, Any] | None = None,
    order: dict[str, Any] | None = None,
) -> str | None:
    """Clasifica el inbound y persiste la señal en `metadata` (mutación).

    Returns ``"deferral"`` / ``"affirmation"`` / ``None``: la lectura de hoy
    (`classify_inbound_purchase_signal`) seguida de la escritura
    (`apply_inbound_purchase_signal`).
    """
    kind, confirmed_by = classify_inbound_purchase_signal(text, interactive=interactive, order=order)
    return apply_inbound_purchase_signal(
        metadata, kind, confirmed_by, now_ms=now_ms, message_id=message_id, text=text
    )


def has_purchase_confirmation(metadata: dict[str, Any]) -> bool:
    """El cliente confirmó la compra en ESTE episodio, o ya hay orden registrada."""
    registered = metadata.get("registered_order")
    if isinstance(registered, dict) and registered.get("success") is True:
        return True
    episode = active_episode(metadata)
    if not episode:
        return False
    draft = episode.get("order_draft")
    return isinstance(draft, dict) and isinstance(draft.get("confirmed_at_ms"), int)


def current_signal(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """La señal del ÚLTIMO inbound (o None si el último inbound no la trajo)."""
    sig = metadata.get(SIGNAL_KEY)
    if not isinstance(sig, dict) or not sig.get("message_id"):
        return None
    if sig.get("message_id") != metadata.get("last_inbound_message_id"):
        return None
    return sig


def is_current_inbound_deferral(metadata: dict[str, Any]) -> bool:
    sig = current_signal(metadata)
    return bool(sig and sig.get("kind") == "deferral")


def closing_blocker(metadata: dict[str, Any], *, require_confirmation: bool) -> str | None:
    """Por qué no puede avanzar el cierre ahora (resumen, registro), o None.

    * ``customer_deferred``: el último mensaje del cliente aplazó (CON-02).
    * ``purchase_not_confirmed`` (solo con `require_confirmation`): hay un
      episodio y nadie confirmó: ni la confirmación anotada en el episodio ni
      un «Confirmar» o un «sí» en este mismo mensaje (CIE-02). El mensaje
      cuenta porque la confirmación solo se anota con el producto en el
      borrador, y un «Confirmar» legítimo no puede quedar frenado.

    Sin episodio no hay con qué juzgar: no frena (falla abierta).
    """
    if is_current_inbound_deferral(metadata):
        return "customer_deferred"
    if not require_confirmation or has_purchase_confirmation(metadata) or not active_episode(metadata):
        return None
    signal = current_signal(metadata)
    if signal and signal.get("kind") == "affirmation":
        return None
    return "purchase_not_confirmed"


def build_deferral_note(
    metadata: dict[str, Any], *, resume_label: str | None = None
) -> str | None:
    """Nota para `plugin_context`: el LLM responde texto breve y no avanza el cierre."""
    sig = current_signal(metadata)
    if not sig or sig.get("kind") != "deferral":
        return None
    quoted = str(sig.get("text") or "").strip()
    note = (
        "[SISTEMA — EL CLIENTE APLAZÓ]: acaba de escribir "
        f"\"{quoted}\". Eso NO es una confirmación: responde con UNA frase "
        "cálida y breve (sin preguntas de venta), NO muestres productos, NO "
        "pidas datos de envío ni confirmes el pedido. Espera a que retome."
    )
    # La cita: el sistema le escribe ESE día — el bot se lo confirma para que
    # sea un compromiso visible ("¡Listo! Te escribimos el lunes 28"). El
    # label lo calcula el ingest (sdk.messagingkit): este módulo lo importan
    # tools, que no pueden arrastrar temporalio (contrato R-DIP).
    if resume_label:
        note += (
            f" Confírmale que le escribimos {resume_label} "
            "(no prometas otra fecha)."
        )
    return note


__all__ = [
    "SIGNAL_KEY",
    "apply_inbound_purchase_signal",
    "build_deferral_note",
    "classify_inbound_purchase_signal",
    "current_signal",
    "detect_deferral",
    "detect_purchase_affirmation",
    "has_purchase_confirmation",
    "is_current_inbound_deferral",
    "register_inbound_purchase_signals",
]
