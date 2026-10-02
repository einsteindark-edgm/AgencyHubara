"""Builtins de las capacidades que deciden ítem por ítem (PAQUETES_DE_DECISION.md
F4): persona, monto, datos, fuera de catálogo y las del egreso por partes
(preámbulo, destinatario por oración, rescate, portavelas).

Un builtin de clase `items` arma la lista (oraciones, párrafos, términos,
datos) con los campos que declara el catálogo; el paquete pregunta lo mismo
por cada ítem (`each:`) y decide con la lista en CEL. Lo que sigue siendo
código es el corte mecánico del texto (no lee el sentido), las reglas de hoy
y los pisos.

Se cargan solo cuando una capacidad del paquete los pide.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.plugins.chats.agent.sales.decisions.capabilities.datos import (
    _LABELS,
    CHECKED_SLOTS,
    PERSONAL_SLOTS,
    _window,
    in_customer_words,
)
from src.plugins.chats.agent.sales.decisions.capabilities.texto import _PERSONA_FLOOR
from src.plugins.chats.agent.sales.decisions.egress import (
    _MAX_PREAMBLE_SENTENCES,
    ORDER_REGISTERED_FALLBACK_FAREWELL,
    _leading_sentences,
    _paragraphs,
    _preamble_of,
    _sentences,
)
from src.plugins.chats.shared.product_truth import unavailable_terms
from src.sdk.textkit import (
    breaks_human_persona,
    looks_like_admin_leak,
    salvage_customer_text,
    strip_model_preamble,
    strip_portavelas_notice,
)

Items = list[dict[str, Any]]


def _numbered(parts: Sequence[str]) -> str:
    return "\n".join(f"[{i}] {part}" for i, part in enumerate(parts, 1))


# ── reglas ──


def breaks_persona(inp: Any) -> tuple[int, ...]:
    """Las oraciones que hoy rompen la persona (`breaks_human_persona`)."""
    return tuple(i for i, part in enumerate(inp.parts) if breaks_human_persona(part))


def unavailable_terms_rule(inp: Any) -> list[str]:
    """Los términos que el cliente pide y no existen en el catálogo (regex de hoy)."""
    return unavailable_terms(inp.text, list(inp.products))


def model_preamble(inp: Any) -> str:
    """La muletilla del meta-prefijo de hoy (`strip_model_preamble`)."""
    return _preamble_of(inp.text, strip_model_preamble(inp.text))


def admin_leak_per_part(inp: Any) -> tuple[int, ...]:
    """Las oraciones que huelen a parte interno (el set básico, por oración)."""
    return tuple(i for i, part in enumerate(inp.parts) if looks_like_admin_leak(part))


def salvage(inp: Any) -> str:
    return salvage_customer_text(inp.text, extended=True)


def _portavelas_applies(inp: Any) -> bool:
    return bool(inp.text) and inp.order_registered and not inp.portavelas_included


def portavelas_notice(inp: Any) -> str:
    """En un pedido sin portavelas, la regla de hoy borra lo que lo nombra."""
    if _portavelas_applies(inp) and "portavela" in inp.text.lower():
        return strip_portavelas_notice(inp.text) or ORDER_REGISTERED_FALLBACK_FAREWELL
    return inp.text


# ── ítems ──


def paragraphs(inp: Any) -> Items:
    return [{"text": part} for part in _paragraphs(inp.text)]


def sentences(inp: Any) -> Items:
    return [{"text": part} for part in _sentences(inp.text)]


def leading_sentences(inp: Any) -> Items:
    """Las oraciones del texto con lo que hace falta para cortar la muletilla:
    `askable` (solo las primeras, nunca la última), `preamble` (lo que se cae
    si se corta hasta esta) y `cut_ok` (lo que queda no empieza a mitad de frase)."""
    text = inp.text
    parts = _leading_sentences(text)
    asked = min(_MAX_PREAMBLE_SENTENCES, len(parts) - 1)
    out: Items = []
    for n, (_start, part) in enumerate(parts, 1):
        kept = text[parts[n][0]:].lstrip() if n < len(parts) else ""
        out.append({
            "text": part, "askable": n <= asked,
            "preamble": _preamble_of(text, kept) if kept else "",
            "cut_ok": bool(kept) and not kept[0].islower(),
        })
    return out


def order_slot_values(inp: Any) -> Items:
    """Los datos de envío y pago que el LLM quiere guardar. `ask`: lo que no
    está en las palabras del cliente (eso se guarda sin preguntar)."""
    said = [text for who, text in _window(inp.events) if who == "cliente"]
    return [
        {"slot": slot, "value": value, "ask": not in_customer_words(slot, value, said),
         "personal": slot in PERSONAL_SLOTS, "label": _LABELS.get(slot, slot)}
        for slot, value in inp.values
        if slot in CHECKED_SLOTS and value.strip()
    ]


def unavailable_term_items(inp: Any) -> Items:
    return [{"term": term} for term in unavailable_terms_rule(inp)]


# ── estado ──


def leading_sentences_state(inp: Any, *, items: Items | None = None) -> str | None:
    """Las primeras oraciones del texto (una sola nunca es solo muletilla)."""
    parts = [item["text"] for item in items or ()]
    if len(parts) < 2:
        return None
    shown = parts[: _MAX_PREAMBLE_SENTENCES + 2]
    return (
        "Texto que el asesor de ventas escribió para enviarle al cliente por WhatsApp, oración por oración:\n"
        + _numbered(shown)
        + ("\n[…]" if len(parts) > len(shown) else "")
    )


def conversation_for_slots(inp: Any, *, items: Items | None = None) -> str | None:
    """Lo último de la conversación, si hay algún dato que preguntar."""
    window = _window(inp.events)
    if not any(item["ask"] for item in items or ()) or not window:
        return None
    lines = [f"[{who}] {text[:400]}" for who, text in window]
    return "Conversación de una tienda con un cliente por WhatsApp (lo último al final):\n" + "\n".join(lines)


def customer_order_text(inp: Any, *, items: Items | None = None) -> str | None:
    """El mensaje del cliente, si la regla propone algún término."""
    if not items:
        return None
    return f"Mensaje del cliente (lo que mostró en una foto va entre corchetes):\n[1] {inp.text.strip()}"


# ── pisos ──


def persona_floor(inp: Any, rule: tuple[int, ...], jev: tuple[int, ...]) -> tuple[int, ...]:
    """Subconjunto de la regla de hoy: con Jev nunca se cae lo que hoy pasa
    (las frases que ninguna lectura puede dejar pasar)."""
    floor = {i for i in rule if i < len(inp.parts) and any(p.search(inp.parts[i]) for p in _PERSONA_FLOOR)}
    return tuple(sorted(set(jev) | floor))


def strong_preamble(inp: Any, rule: str, jev: str) -> str:
    """Los prefijos que casi nunca abren una oración legítima siempre se van."""
    strong = _preamble_of(inp.text, strip_model_preamble(inp.text, strong_only=True))
    return strong if len(strong) > len(jev or "") else jev
