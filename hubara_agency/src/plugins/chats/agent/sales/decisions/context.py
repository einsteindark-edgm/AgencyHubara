"""Contexto del turno para Jev (diseño v2 §01, fase F1). PURO.

Sin esto Jev lee un «Si» a ciegas. El motor le da, en una sección separada de
los mensajes de ESTE turno:

* `customer_window`: lo que el cliente vio antes del turno, leído del
  historial del vault (el mismo JSONL del dashboard): textos del bot, notas de
  botones/tarjetas/formularios (`flush_ui_intents._build_history_event`),
  mensajes del equipo. Sin los mensajes de esta ráfaga (por `wamid`; sin ids,
  los últimos `burst_size` del cliente). Hasta 8 eventos y ~1.800
  caracteres; un mensaje largo se corta por el PRINCIPIO (la pregunta del bot
  suele ir al final). El historial ya trae la ráfaga (el ingest la guarda
  antes del turno, y el sandbox del laboratorio igual): de ella sale la cita.
* `order_facts`: la etapa que calcula el código, los ítems (producto, color,
  aroma, cantidad) y la ciudad. Dirección, teléfono, quien recibe y pago solo
  como «dado»/«falta»: nunca el dato.
* `bot_asked_known`: si lo último que vio el cliente fue la tarjeta de
  confirmación o el formulario, el código ya sabe qué se preguntó y no hace
  falta preguntarle a Jev.

Sin I/O: quien llama (la activity) lee el vault y pasa los eventos.
"""
from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.plugins.chats.shared.draft_items import draft_items, missing_variants
from src.plugins.chats.shared.funnel import active_episode

MAX_EVENTS = 8
MAX_CHARS = 1800
MAX_LINE_CHARS = 500

#: Lo que el cliente tenía delante cuando la tarjeta o el formulario fueron lo
#: último que vio: el código ya sabe qué se le preguntó.
BOT_ASKED_BY_COMPONENT: dict[str, str] = {
    "order_confirmation": "confirmar_compra",
    "shipping_flow": "confirmar_dato_envio",
}

STAGE_LABELS: dict[str, str] = {
    "etapa_descubrimiento": "descubrimiento",
    "etapa_variantes": "variantes",
    "etapa_datos_envio": "datos de envío",
    "etapa_cierre": "cierre",
    "etapa_postcierre": "postcierre",
}

# Datos de envío del borrador que Jev no necesita (decisión 2 del plan del
# laboratorio). Los nombres se tapan también palabra por palabra: el cliente o
# el asesor repiten solo el nombre ("Listo Carolina"). El barrio y la
# dirección, completos: partirlos taparía palabras del producto ("alto").
_PERSONAL_SLOTS = ("nombre_recibe", "direccion", "barrio", "telefono")
_NAME_SLOTS = ("nombre_recibe",)
_NAME_TOKEN_RE = re.compile(r"[^\W\d_]{3,}")

# Datos personales: solo si están o faltan (en este orden, con su género).
_PERSONAL = (
    ("direccion", "Dirección", "dada"),
    ("telefono", "Teléfono", "dado"),
    ("nombre_recibe", "Quien recibe", "dado"),
    ("metodo_pago", "Método de pago", "dado"),
)


@dataclass(frozen=True)
class Window:
    lines: tuple[str, ...] = ()
    last_component: str | None = None
    bot_asked_known: str | None = None
    quoted: str | None = None


@dataclass(frozen=True)
class ViewedProduct:
    """El producto desde el que escribe el cliente, ya resuelto contra el
    catálogo por el ingest: la ficha del catálogo de WhatsApp («Enviar mensaje
    a la empresa», `from_catalog`) o la página de la web (`ref: HUB-…`)."""

    title: str
    handle: str
    from_catalog: bool
    variant: str | None = None


@dataclass(frozen=True)
class TurnContext:
    """Lo que el motor le da a Jev además de la ráfaga."""

    window: Window = Window()
    facts: tuple[str, ...] = ()
    # F6 · etapas: la etapa que calcula el código, lo que falta en ella y
    # cuántos turnos seguidos lleva sin un dato nuevo.
    stage: str | None = None
    missing: tuple[str, ...] = ()
    stagnant: int = 0
    courtesy: bool = False
    # El producto desde el que escribe el cliente (2026-10-07: sin él, la
    # guía de descubrimiento le pedía al LLM «ayúdale a escoger un producto»
    # al cliente que acababa de escribir desde la ficha de la Calabaza).
    viewed_product: ViewedProduct | None = None

    def when_facts(self) -> dict[str, Any]:
        """Los hechos con que se filtran las preguntas (`when` del cuestionario)."""
        return {
            "has_context": bool(self.window.lines),
            "bot_asked_known": self.window.bot_asked_known,
            "stage": self.stage,
        }


def redact_terms_from_slots(slots: Mapping[str, Any]) -> list[str]:
    """Lo que hay que tapar de ESTE cliente antes de que el turno salga hacia
    Jev, desde las casillas del borrador (lo usan la activity y el banco de
    referencia del laboratorio)."""
    terms: set[str] = set()
    for key in _PERSONAL_SLOTS:
        value = slots.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        terms.add(value.strip())
        if key in _NAME_SLOTS:
            terms.update(_NAME_TOKEN_RE.findall(value))
    return sorted(terms)


def _speaker(event: Mapping[str, Any]) -> str | None:
    role = event.get("role")
    if role == "user":
        return "cliente"
    if role == "assistant":
        return "equipo" if event.get("sender") == "human" else "asesor"
    return None


def _clip(text: str) -> str:
    """Un mensaje largo se corta por el principio: se queda el final."""
    text = " ".join(text.split())
    return text if len(text) <= MAX_LINE_CHARS else "…" + text[-(MAX_LINE_CHARS - 1):].lstrip()


#: Sufijos de los ids sintéticos de los reentries de visión y transcripción
#: (los mismos del ingest): el historial guarda el id real.
SYNTHETIC_WAMID_SUFFIXES = ("_transcribed", "_vision")


def real_wamid(wamid: str) -> str:
    """El id real de WhatsApp de un mensaje, sin el sufijo del reentry."""
    for suffix in SYNTHETIC_WAMID_SUFFIXES:
        wamid = wamid.removesuffix(suffix)
    return wamid


def _burst_start(events: Sequence[Mapping[str, Any]], burst_wamids: Collection[str], burst_size: int) -> int:
    """Índice donde empieza la ráfaga actual (los eventos de cliente del final)."""
    end = len(events)
    if burst_wamids:
        while end > 0 and events[end - 1].get("role") == "user" and events[end - 1].get("wamid") in burst_wamids:
            end -= 1
        return end
    removed = 0
    while end > 0 and removed < burst_size and events[end - 1].get("role") == "user":
        end -= 1
        removed += 1
    return end


def customer_window(
    events: Sequence[Mapping[str, Any]], *, burst_wamids: Collection[str], burst_size: int = 0
) -> Window:
    """Lo que el cliente vio antes de este turno (ver el docstring del módulo)."""
    start = _burst_start(events, {real_wamid(str(w)) for w in burst_wamids}, burst_size)
    burst = events[start:]
    quoted = None
    for event in burst:
        reply_to = event.get("reply_to")
        if isinstance(reply_to, Mapping) and str(reply_to.get("text") or "").strip():
            quoted = _clip(str(reply_to["text"]))
    before = [e for e in events[:start] if _speaker(e) and str(e.get("content") or "").strip()]
    last_bot = next((e for e in reversed(before) if e.get("role") == "assistant"), None)
    last_component = str(last_bot.get("component_kind")) if last_bot and last_bot.get("kind") == "ui_component" else None
    lines: list[str] = [f"[{_speaker(e)}] {_clip(str(e['content']))}" for e in before[-MAX_EVENTS:]]
    while lines and sum(len(line) for line in lines) > MAX_CHARS:
        lines.pop(0)
    return Window(
        lines=tuple(lines),
        last_component=last_component,
        bot_asked_known=BOT_ASKED_BY_COMPONENT.get(last_component or ""),
        quoted=quoted,
    )


def _item_line(k: int, item: Mapping[str, Any]) -> str:
    parts = [str(item.get(f)).strip() for f in ("producto", "diseno", "color", "aroma") if str(item.get(f) or "").strip()]
    qty = item.get("cantidad")
    if isinstance(qty, (int, float)) and not isinstance(qty, bool) or (isinstance(qty, str) and qty.strip().isdigit()):
        n = int(qty)
        parts.append(f"{n} unidad" if n == 1 else f"{n} unidades")
    return f"Ítem {k}: " + ", ".join(parts)


def order_facts(metadata: Mapping[str, Any], *, stage: str) -> tuple[str, ...]:
    """Los hechos del pedido, sin datos personales (ver el docstring del módulo)."""
    facts = [f"Etapa: {STAGE_LABELS.get(stage, stage)}"]
    episode = active_episode(dict(metadata))
    draft = (episode or {}).get("order_draft")
    if not isinstance(draft, dict):
        return tuple(facts)
    slots = draft.get("slots") if isinstance(draft.get("slots"), dict) else {}
    facts += [_item_line(k, item) for k, item in enumerate(draft_items(draft), 1)]
    city = str(slots.get("ciudad") or "").strip()
    facts.append(f"Ciudad: {city}" if city else "Ciudad: falta")
    facts.append(
        " · ".join(
            f"{label}: {given if str(slots.get(key) or '').strip() else 'falta'}" for key, label, given in _PERSONAL
        )
    )
    facts.append("Compra confirmada: " + ("sí" if isinstance(draft.get("confirmed_at_ms"), int) else "no"))
    return tuple(facts)


#: Datos de envío que exige la etapa (los mismos que `resolve_funnel_stage`).
SHIPPING_SLOTS: tuple[str, ...] = ("ciudad", "direccion", "telefono", "nombre_recibe", "metodo_pago")


def missing_for_stage(metadata: Mapping[str, Any], stage: str) -> tuple[str, ...]:
    """Lo que falta para salir de la etapa, según el borrador (F6)."""
    episode = active_episode(dict(metadata))
    draft = (episode or {}).get("order_draft")
    slots = draft.get("slots") if isinstance(draft, dict) and isinstance(draft.get("slots"), dict) else {}
    if stage == "etapa_datos_envio":
        return tuple(k for k in SHIPPING_SLOTS if not str(slots.get(k) or "").strip())
    if stage == "etapa_variantes":
        items = draft_items(draft) if isinstance(draft, dict) else []
        out: list[str] = []
        for k, item in enumerate(items, 1):
            for field in missing_variants(draft if isinstance(draft, dict) else None, item):
                out.append(field if len(items) == 1 else f"{field} (ítem {k})")
        return tuple(out)
    return ()


def stagnant_turns(
    traces: Sequence[Mapping[str, Any]], *, episode_id: str, draft: Mapping[str, Any] | None = None
) -> int:
    """Turnos seguidos al final, del episodio, en la misma etapa y sin un dato
    nuevo en el borrador (la traza guarda la etapa y el borrador de cada
    turno). Con `draft` (el borrador de ahora), un dato que llegó después del
    último turno corta la cuenta."""
    own = [t for t in traces if t.get("episode_id") == episode_id and t.get("trigger", "customer") in ("customer", "handoff")]
    if not own:
        return 0
    last = own[-1]
    if draft is not None and dict(draft) != (last.get("draft") or {}):
        return 0
    count = 0
    for trace in reversed(own):
        if trace.get("stage_out") != last.get("stage_out") or trace.get("draft") != last.get("draft"):
            break
        count += 1
    return count

