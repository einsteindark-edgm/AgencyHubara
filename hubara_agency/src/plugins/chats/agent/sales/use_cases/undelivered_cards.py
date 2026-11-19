"""Lo que no le llegó al cliente: el turno siguiente lo sabe (celda local de Acktos, 2026-10-23).

Las tools de UI (`tools/ui_intents.py`) le responden al LLM al ENCOLAR
(«Producto enviado al cliente: …»); el envío real lo hace el flush después del
texto del turno. Si el envío falla, el LLM lo sigue dando por hecho: en la
celda local de Acktos la foto de un producto tenía una URL http, WhatsApp no la
aceptó y en el turno siguiente el bot le dijo al cliente «Por ahora solo tengo
esa foto…» como si la hubiera visto.

El flush anota cada envío que falló como un aviso pendiente, en palabras
(`metadata.json[undelivered_ui_intents]`); el `build_prompt` del turno
siguiente lo pone en el contexto del turno y lo marca como dicho; el flush del
final de ESE turno lo borra. Así llega una sola vez, también si el turno se
reinicia (una ráfaga del cliente vuelve a armar el prompt antes de terminar).

Funciones puras sobre el metadata: el I/O lo hacen las activities. Tuteo
(guard `test_no_voseo_in_agent_strings.py`).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: Los avisos pendientes en `metadata.json`: `[{what, at_ms, told?}]`.
UNDELIVERED_KEY = "undelivered_ui_intents"

#: Un aviso que ningún turno cerró (el turno de abandono no hace flush, el LLM
#: se rindió) vence con la ventana de 24 h de WhatsApp: no vuelve al otro día,
#: fuera de contexto.
NOTICE_TTL_MS = 24 * 60 * 60 * 1000

#: Cada tarjeta que despacha el flush (el `kind` del intent) en palabras. Una
#: que no esté igual se avisa, con el nombre genérico.
_CARD_WORDS = {
    "product_detail": "la foto del producto",
    "product_gallery": "las fotos del producto",
    "products_list": "la lista de productos",
    "categories": "el menú de categorías del catálogo",
    "variant_picker": "el selector de opciones",
    "quick_replies": "los botones de respuesta",
    "shipping_flow": "el formulario de datos de envío",
    "shipping_rates": "el mensaje con las tarifas de envío",
    "order_confirmation": "el resumen del pedido para confirmar",
    "payment_instructions": "los datos de pago",
    "contact_card": "la tarjeta de contacto del asesor",
    "cta_url": "el botón con el enlace",
    "reaction": "tu reacción a su mensaje",
}
_ANY_CARD = "un mensaje con botones o tarjeta"


def card_words(kind: str | None, params: Mapping[str, Any], *, page: int | None = None, pages: int | None = None) -> str:
    """Qué tarjeta es, en palabras, con el producto si el intent lo trae y la
    página si lo que no llegó es una página de la lista."""
    words = _CARD_WORDS.get(str(kind or ""), _ANY_CARD)
    title = params.get("title")
    if isinstance(title, str) and title.strip():
        words += f" «{title.strip()}»"
    if page and pages:
        words = f"la página {page} de {pages} de {words}"
    return words


def _notices(metadata: Mapping[str, Any], *, now_ms: int | None = None) -> list[dict[str, Any]]:
    """Los avisos bien formados; con `now_ms`, solo los vigentes."""
    notices = [n for n in metadata.get(UNDELIVERED_KEY) or [] if isinstance(n, dict) and isinstance(n.get("what"), str)]
    if now_ms is None:
        return notices
    return [n for n in notices if isinstance(n.get("at_ms"), int) and now_ms - n["at_ms"] <= NOTICE_TTL_MS]


def record_undelivered(metadata: dict[str, Any], whats: Iterable[str], *, now_ms: int) -> dict[str, Any]:
    """Agrega los envíos fallidos (`whats`, en palabras) a los avisos pendientes."""
    added = [{"what": what, "at_ms": now_ms} for what in whats]
    if added:
        metadata[UNDELIVERED_KEY] = [*_notices(metadata), *added]
    return metadata


def undelivered_note(metadata: Mapping[str, Any], *, now_ms: int) -> str | None:
    """La nota del turno con lo que no le llegó al cliente (None sin avisos
    vigentes)."""
    whats = [n["what"] for n in _notices(metadata, now_ms=now_ms)]
    if not whats:
        return None
    items = "\n".join(f"- {what}" for what in whats)
    return (
        "[SISTEMA — NO LE LLEGÓ AL CLIENTE] Esto falló al enviarse y el cliente NO lo recibió, "
        "aunque la tool te haya dicho que se envió:\n"
        f"{items}\n"
        "El cliente no lo vio: no hables de eso como si lo hubiera visto. Si sigue siendo relevante, "
        "vuelve a enviarlo o díselo en texto."
    )


def tell_undelivered(metadata: dict[str, Any], *, now_ms: int) -> str | None:
    """La nota del turno, y marca sus avisos como dichos: siguen en el contexto
    hasta que el flush del final del turno los borra (`forget_told`)."""
    note = undelivered_note(metadata, now_ms=now_ms)
    if note:
        metadata[UNDELIVERED_KEY] = [{**n, "told": True} for n in _notices(metadata, now_ms=now_ms)]
    return note


def any_told(metadata: Mapping[str, Any]) -> bool:
    """¿Algún aviso ya entró al contexto de un turno?"""
    return any(n.get("told") is True for n in _notices(metadata))


def forget_told(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Mutator del flush del final del turno: borra los avisos que el turno ya
    vio. None (no escribe) si no hay ninguno."""
    notices = _notices(metadata)
    kept = [n for n in notices if n.get("told") is not True]
    if len(kept) == len(notices):
        return None
    metadata[UNDELIVERED_KEY] = kept
    return metadata
