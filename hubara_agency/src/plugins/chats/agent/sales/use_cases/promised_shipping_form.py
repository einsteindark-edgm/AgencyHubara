"""El formulario de envío que el texto del bot promete (incidente 2026-10-09).

Con el producto, la ciudad y la forma de pago elegidos, el bot V2 contestó
bien y cerró con «te paso el formulario para los datos de envío», pero no
llamó `request_shipping_details`; en el turno siguiente lo volvió a prometer.
El operador tuvo que mandarlo a mano. Criterio del operador: la respuesta
estaba bien, solo faltó el formulario.

La red (`ensure_promised_handoff_activity`, antes de enviar el texto) usa este
módulo: ¿el texto lo promete?, ¿ya salió o está en la cola en este episodio?,
¿con qué ítems del borrador se pide? Funciones puras sobre el metadata y las
filas del registro de entregas: el I/O lo hace la activity.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

from src.plugins.chats.agent.sales.use_cases.quantity_capture import parse_leading_quantity
from src.plugins.chats.shared.draft_items import draft_items, find_product
from src.plugins.chats.shared.funnel import active_episode

SHIPPING_FORM_KIND = "shipping_flow"

_FORM = r"(?:(?:el|un|nuestro|este)\s+)?(?:(?:link|enlace)\s+(?:del|de)\s+)?formulario\b"
_PROMISE_RE = re.compile(
    # «te paso / envío / mando / comparto el formulario», «te voy a enviar el formulario»
    rf"\b(?:te|le|les)\s+(?:paso|envio|mando|comparto|dejo|adjunto|hago\s+llegar"
    rf"|(?:voy|vamos)\s+a\s+(?:pasar|enviar|mandar|compartir|dejar))\s+{_FORM}"
    # «ahí te va el formulario», «aquí te dejo el formulario»
    rf"|\b(?:aqui|ahi|aca)\s+(?:te\s+)?(?:va|dejo|envio|mando|paso)\s+{_FORM}"
)
# Una oración termina en . ! ? o salto de línea; la que pregunta no promete.
_SENTENCE_RE = re.compile(r"[^.!?\n]*[.!?\n]?")


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFD", text or "")
    return " ".join("".join(c for c in folded if not unicodedata.combining(c)).lower().split())


def promises_shipping_form(text: str | None) -> bool:
    """¿El texto le dice al cliente que AHORA le manda el formulario de envío?

    Una pregunta («¿te paso el formulario?») no promete, y hablar del
    formulario («ya te envié el formulario», «llena el formulario») tampoco.
    """
    for sentence in _SENTENCE_RE.findall(text or ""):
        if "?" in sentence or "¿" in sentence:
            continue
        if _PROMISE_RE.search(_plain(sentence)):
            return True
    return False


def shipping_form_in_episode(metadata: Mapping[str, Any], delivered: Iterable[Mapping[str, Any]]) -> bool:
    """El formulario ya está en la cola o ya salió en el episodio activo
    (`delivered`: filas del registro de entregas, `ui_intents_delivered.jsonl`).
    Sale una sola vez por episodio: un formulario de un episodio anterior no
    cuenta."""
    queued = metadata.get("pending_ui_intents") or []
    if any(isinstance(i, Mapping) and i.get("kind") == SHIPPING_FORM_KIND for i in queued):
        return True
    episode = active_episode(dict(metadata)) or {}
    started = episode.get("started_at_ms")
    since = started if isinstance(started, int) else 0
    return any(
        row.get("kind") == SHIPPING_FORM_KIND
        and row.get("ok") is True
        and isinstance(row.get("at_ms"), int)
        and row["at_ms"] >= since
        for row in delivered
    )


def shipping_form_items(metadata: Mapping[str, Any], products: list[Any]) -> list[dict[str, Any]] | None:
    """`items` de `request_shipping_details` ({handle, quantity}) desde el
    borrador del episodio activo, o None si algún producto no está en el
    catálogo (no se adivina). Sin cantidad anotada, una unidad."""
    episode = active_episode(dict(metadata)) or {}
    draft = episode.get("order_draft")
    lines = draft_items(draft if isinstance(draft, dict) else None)
    if not lines:
        return None
    items: list[dict[str, Any]] = []
    for line in lines:
        product = find_product(products, line.get("producto"))
        if product is None:
            return None
        quantity = parse_leading_quantity(str(line.get("cantidad") or "")) or 1
        items.append({"handle": product.handle, "quantity": quantity})
    return items
