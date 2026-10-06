"""Lo que el cliente LEE dentro de cada tarjeta que redacta el LLM.

Una sola lista para la verificación del turno (③: ¿la respuesta atiende cada
asunto?) y para el scorecard (el primer texto que leyó el cliente, los montos
que vio). Caso 4567 del laboratorio (caso-fotos-0929-r3, turno 1 del bot
nuevo): el saludo con la marca iba en el texto de la lista, lo único que el
cliente lee con el menú; la verificación y la revisión solo leían los textos
sueltos, así que el saludo "faltaba".

Solo el texto que acompaña la tarjeta. Los botones son etiquetas, no la
respuesta. El motivo de `send_contact_card` no está: el envío solo manda el
contacto. Puro y sin dependencias: lo importa la fachada del motor, que corre
dentro del workflow.

Las tarjetas cuyo texto arma el CÓDIGO (el mensaje del formulario de envío, el
resumen del pedido, las tarifas de envío) lo devuelven en el envelope de su
tool, en `customer_text` (`envelope_card_text`). Incidente 2026-10-06 (turno
9): el cliente leyó el mensaje del formulario y ni la calificación ni la
verificación lo veían.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

#: La clave del envelope de una tool con el texto que el código armó para el
#: cliente junto a la tarjeta.
CUSTOMER_TEXT_KEY = "customer_text"

CARD_TEXT_ARGS: Mapping[str, tuple[str, ...]] = {
    "present_products": ("intro_text",),  # el cuerpo de la lista
    "present_variant_picker": ("intro_text",),  # el cuerpo del selector
    "present_product_detail": ("caption_suffix",),  # debajo del título y el precio de la foto
    "send_quick_replies": ("body",),  # el texto sobre los botones
    "send_cta_url": ("body_text",),  # el texto sobre el botón con enlace
}


def card_texts(name: str, args: Mapping[str, Any]) -> list[str]:
    """Los textos que el cliente lee con la tarjeta `name`, en su orden."""
    out: list[str] = []
    for key in CARD_TEXT_ARGS.get(name, ()):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            out.append(value.strip())
    return out


def envelope_card_text(result: Any) -> str | None:
    """El texto que el código armó para el cliente con la tarjeta: el
    `customer_text` del envelope JSON de la tool. None sin envelope legible
    (texto plano, JSON cortado) o sin ese texto. No mira si la tool se negó:
    eso lo decide quien llama."""
    if not isinstance(result, str):
        return None
    try:
        payload = json.loads(result)
    except ValueError:
        return None
    value = payload.get(CUSTOMER_TEXT_KEY) if isinstance(payload, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None
