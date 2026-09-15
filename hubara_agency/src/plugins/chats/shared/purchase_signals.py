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
        r"\bte aviso\b",
        r"\bte escribo\b",
        r"\bte confirmo\b",
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
    return bool(_AFFIRMATION_START.search(norm) or _AFFIRMATION_ANY.search(norm))


def _is_confirm_button(interactive: dict[str, Any] | None) -> bool:
    if not isinstance(interactive, dict):
        return False
    if interactive.get("type") not in (None, "button_reply"):
        return False
    button_id = str(interactive.get("id") or "").lower()
    title = _normalize(str(interactive.get("title") or ""))
    return any(button_id.startswith(p) for p in _CONFIRM_BUTTON_IDS) or "confirmar" in title


def register_inbound_purchase_signals(
    metadata: dict[str, Any],
    text: str | None,
    *,
    now_ms: int,
    message_id: str | None,
    interactive: dict[str, Any] | None = None,
) -> str | None:
    """Clasifica el inbound y persiste la señal en `metadata` (mutación).

    Returns ``"deferral"`` / ``"affirmation"`` / ``None``. Una afirmación con
    producto ya elegido en el draft del episodio activo marca la confirmación
    de compra (`order_draft.confirmed_at_ms` + `confirmed_by`).
    """
    kind: str | None = None
    confirmed_by = "text"
    if _is_confirm_button(interactive):
        kind, confirmed_by = "affirmation", "button"
    elif detect_deferral(text):
        kind = "deferral"
    elif detect_purchase_affirmation(text):
        kind = "affirmation"

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
        draft = (episode or {}).get("order_draft") if episode else None
        slots = draft.get("slots") if isinstance(draft, dict) else None
        if isinstance(slots, dict) and str(slots.get("producto") or "").strip():
            draft["confirmed_at_ms"] = now_ms
            draft["confirmed_by"] = confirmed_by
    return kind


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


def build_deferral_note(metadata: dict[str, Any]) -> str | None:
    """Nota para `plugin_context`: el LLM responde texto breve y no avanza el cierre."""
    sig = current_signal(metadata)
    if not sig or sig.get("kind") != "deferral":
        return None
    quoted = str(sig.get("text") or "").strip()
    return (
        "[SISTEMA — EL CLIENTE APLAZÓ]: acaba de escribir "
        f"\"{quoted}\". Eso NO es una confirmación: responde con UNA frase "
        "cálida y breve (sin preguntas de venta), NO muestres productos, NO "
        "pidas datos de envío ni confirmes el pedido. Espera a que retome."
    )


__all__ = [
    "SIGNAL_KEY",
    "build_deferral_note",
    "current_signal",
    "detect_deferral",
    "detect_purchase_affirmation",
    "has_purchase_confirmation",
    "is_current_inbound_deferral",
    "register_inbound_purchase_signals",
]
