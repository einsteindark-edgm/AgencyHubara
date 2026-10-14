"""La respuesta del formulario de calificación (puro).

El Flow `review_v1` vuelve como `nfm_reply` con `response_json`: estrellas
(`rating` "1".."5"), comentario, foto (PhotoPicker) y permiso para publicar.
El `flow_token` que pusimos al enviarlo dice de qué pedido es.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

FLOW_TOKEN_PREFIX = "review:"


@dataclass(frozen=True)
class ReviewSubmission:
    order_id: str
    rating: int
    comment: str | None
    photo_media_ids: tuple[str, ...]
    publish_consent: bool


#: Nombre de relleno que pone el registro cuando no hay nombre real.
_PLACEHOLDER_NAMES = {"cliente whatsapp", "cliente", "cliente sin nombre"}
#: Una palabra de un nombre: letras (con tildes), guion y apóstrofo.
_NAME_WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
_ANONYMOUS = "Cliente de Hubara"
_MAX_COMMENT = 1000


def review_flow_token(order_id: str, nonce: str) -> str:
    """`review:<pedido>:<nonce>` — vuelve en la respuesta del Flow."""
    return f"{FLOW_TOKEN_PREFIX}{order_id}:{nonce}"


def order_id_from_flow_token(token: str | None) -> str | None:
    """El pedido de un token de calificación, o None si el token es de otro
    Flow (el de datos de envío usa `shipping_…`)."""
    if not isinstance(token, str) or not token.startswith(FLOW_TOKEN_PREFIX):
        return None
    order_id = token[len(FLOW_TOKEN_PREFIX):].split(":", 1)[0]
    return order_id or None


def parse_review_response(response: dict[str, Any]) -> ReviewSubmission | None:
    """`response_json` del Flow → reseña, o None si no es una calificación
    válida (otro Flow, estrellas fuera de 1..5)."""
    order_id = order_id_from_flow_token(response.get("flow_token"))
    if order_id is None:
        return None
    try:
        rating = int(str(response.get("rating")).strip())
    except (TypeError, ValueError):
        return None
    if not 1 <= rating <= 5:
        return None
    comment = str(response.get("comment") or "").strip()[:_MAX_COMMENT] or None
    return ReviewSubmission(
        order_id=order_id,
        rating=rating,
        comment=comment,
        photo_media_ids=_photo_ids(response.get("photo")),
        publish_consent=_truthy(response.get("publish_consent")),
    )


def _photo_ids(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    ids = []
    for item in raw:
        if isinstance(item, dict):
            media_id = item.get("id") or item.get("media_id")
            if isinstance(media_id, str) and media_id:
                ids.append(media_id)
    return tuple(ids)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "si", "sí"}


def reward_percent(submission: ReviewSubmission | None) -> int:
    """5 % por calificar; 10 % con foto y permiso para publicarla."""
    if submission is None:
        return 0
    if submission.photo_media_ids and submission.publish_consent:
        return 10
    return 5


def author_display(full_name: str | None) -> str:
    """«Ana G.»: nombre de pila + inicial del apellido (Ley 1581: lo mínimo).

    Sale en la web y en el JSON-LD: solo palabras hechas de letras. Un correo,
    etiquetas o un nombre de relleno quedan anónimos."""
    text = str(full_name or "").strip()
    if "@" in text or text.lower() in _PLACEHOLDER_NAMES:
        return _ANONYMOUS
    parts = [p for p in text.split() if _NAME_WORD.fullmatch(p)]
    if not parts:
        return _ANONYMOUS
    first = parts[0].capitalize()
    if len(parts) == 1:
        return first
    return f"{first} {parts[-1][0].upper()}."


def thanks_text(*, rating: int, percent: int, expires_label: str) -> str:
    """Lo que recibe el cliente al calificar (va con el botón a Google).

    El cupón y la invitación a Google van con CUALQUIER calificación: Google
    prohíbe pedir reseña solo a los contentos. Con 1–3 estrellas además se
    promete al equipo (el plugin lo avisa).
    """
    coupon = (
        f"Tu {percent} % para la próxima compra ya quedó guardado en este número "
        f"(vence el {expires_label}). Cuando nos escribas, lo aplicamos solo."
    )
    google = "¿Nos ayudas contándolo también en Google? A otras personas les sirve para conocernos."
    if rating >= 4:
        opener = f"¡Gracias por tus {rating} estrellas! ⭐"
    else:
        opener = (
            "Gracias por contarnos, de verdad. Sentimos que no fue lo que esperabas: "
            "alguien del equipo te escribe hoy para ayudarte."
        )
    return f"{opener}\n\n{coupon}\n\n{google}"
