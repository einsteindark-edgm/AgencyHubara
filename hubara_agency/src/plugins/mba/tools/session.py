"""Tools de ESCRITURA del connector (D1.2b): delegan por cast al contrato
``session-actions@v1`` de chats (``api/chats_cast.py``).

Cada tool: valida su lista cerrada (lo que el ``request_definition`` de Meta
no puede expresar), traduce los parámetros de Meta al body del contrato y
devuelve un envelope estable al agente. Un fallo del cast es un error
explícito con código cerrado (``chats_unavailable`` / ``chats_timeout`` /
``rejected``), nunca un 500 hacia Meta: el agente sabe qué decirle al cliente
y cuándo pasar el caso a un colega.

Regla #241 (el envío nunca es definitivo) en la respuesta de ``register_order``:
``subtotal_cop`` siempre; ``shipping_cop``/``total_cop`` SOLO con pago
anticipado o link (marcados como tarifa mínima); con contra entrega no viaja
ningún total (el envío se paga al recibir y la transportadora lo recalcula).
"""

from __future__ import annotations

import unicodedata
from typing import Any, Awaitable, Callable

from fastapi import HTTPException, Request
from loguru import logger

__all__ = [
    "REASON_CATEGORIES",
    "TAGS",
    "SessionActionCall",
    "escalate_to_human",
    "manage_conversation_tag",
    "normalize_payment_method",
    "register_order",
    "set_order_slot",
]

#: ``(request, session_key, action, body) -> respuesta del contrato``.
SessionActionCall = Callable[
    [Request | None, str, str, dict[str, Any]], Awaitable[dict[str, Any]]
]

#: MBA solo PROPONE estas dos; el estado de un pedido lo lleva Hubara (§D1.3).
TAGS: tuple[str, ...] = ("INTERESADO", "RECHAZO")

#: Misma lista que el ``request_definition`` autorado (test lo verifica).
REASON_CATEGORIES: tuple[str, ...] = (
    "BULK_ORDER",
    "DISCOUNT_REQUEST",
    "WHOLESALE_B2B",
    "CORPORATE_EVENT",
    "CUSTOMIZATION",
    "POST_SALE_ISSUE",
    "SHIPPING_ISSUE",
    "HEALTH_SAFETY",
    "RITUAL_GUIDANCE",
    "INTERNATIONAL",
    "PAYMENT_EDGECASE",
    "EXPLICIT_REQUEST",
    "CHECKOUT_VERIFY_FAILED",
    "CATALOG_GAP",
    "ORDER_REGISTRATION_FAILED",
)

#: Valores que Meta envía (``metodo_pago``) → método del contrato de chats.
PAYMENT_METHODS: dict[str, str] = {
    "contra_entrega": "cash_on_delivery",
    "anticipado": "transfer",
    "link_de_pago": "payment_link",
}
_CASH_ALIASES = (
    "contra_entrega",
    "contraentrega",
    "contra entrega",
    "cash_on_delivery",
)
_LINK_ALIASES = ("link_de_pago", "link de pago", "payment_link", "link")
_TRANSFER_ALIASES = (
    "anticipado",
    "pago anticipado",
    "nequi",
    "llave",
    "transfer",
    "transferencia",
)

_SHIPPING_NOTE_COD = (
    "Con contra entrega el envío se paga al recibir: la transportadora recalcula el valor "
    "antes de despachar. No des un total con envío; cita solo el subtotal de productos."
)


def _fold(text: str) -> str:
    stripped = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", str(text))
        if not unicodedata.combining(ch)
    )
    return " ".join(stripped.casefold().replace("_", " ").split())


def normalize_payment_method(raw: Any) -> str | None:
    """``contra_entrega`` / ``anticipado`` / ``link_de_pago`` (y variantes
    razonables) → método del contrato; ``None`` si no se reconoce."""
    folded = _fold(raw or "")
    if not folded:
        return None
    for alias in _CASH_ALIASES:
        if _fold(alias) in folded:
            return "cash_on_delivery"
    for alias in _LINK_ALIASES:
        if _fold(alias) in folded:
            return "payment_link"
    for alias in _TRANSFER_ALIASES:
        if _fold(alias) in folded:
            return "transfer"
    return None


async def _call(
    chats: SessionActionCall,
    request: Request | None,
    session_key: str,
    action: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """El cast, con sus fallos convertidos a envelopes explícitos (códigos cerrados)."""
    try:
        return await chats(request, session_key, action, body)
    except HTTPException as exc:
        status = exc.status_code
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        logger.warning(
            "[mba] cast {} session={} → {}: {}", action, session_key, status, detail
        )
        if status == 502:
            return {
                "error": "chats_unavailable",
                "applied": False,
                "message": "La operación NO se aplicó (servicio no disponible). Reintenta una vez; si se repite, pasa el caso a un colega.",
            }
        if status == 504:
            return {
                "error": "chats_timeout",
                "applied": "unknown",
                "message": "El servicio no respondió a tiempo; la operación PUEDE haberse aplicado. No la repitas: pasa el caso a un colega.",
            }
        if status == 409 and detail.startswith("already_human"):
            return {
                "error": "already_human",
                "applied": False,
                "message": "Un colega del equipo ya tiene esta conversación. No etiquetes, no escales y no respondas más en este chat.",
            }
        if 400 <= status < 500:
            out: dict[str, Any] = {
                "error": "rejected",
                "status": status,
                "applied": False,
                "message": "La operación fue rechazada; corrige los datos según `detail` o pasa el caso a un colega.",
            }
            # Solo el detail de validación/precondición (409/422) le sirve al
            # agente; el de auth (401/403) describe infraestructura, no viaja.
            if status in (409, 422):
                out["detail"] = detail[:300]
            return out
        return {
            "error": "chats_error",
            "status": status,
            "applied": "unknown",
            "message": "Error del servicio; no repitas la operación: pasa el caso a un colega.",
        }


async def set_order_slot(
    chats: SessionActionCall,
    request: Request | None,
    *,
    session_key: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    return await _call(chats, request, session_key, "draft", dict(params))


async def register_order(
    chats: SessionActionCall,
    request: Request | None,
    *,
    session_key: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    method = normalize_payment_method(params.get("metodo_pago"))
    if method is None:
        return {
            "error": "invalid_payment_method",
            "accepted": list(PAYMENT_METHODS),
            "message": "metodo_pago debe ser contra_entrega, anticipado o link_de_pago. Confirma con el cliente y vuelve a llamar.",
        }
    items = []
    for it in params.get("items") or []:
        entry: dict[str, Any] = {
            "handle": it.get("handle"),
            "quantity": it.get("quantity"),
        }
        if it.get("variant_label"):
            entry["variant_label"] = it["variant_label"]
        items.append(entry)
    body = {
        "items": items,
        "shipping": {
            "city": params.get("ciudad"),
            "neighborhood": params.get("barrio") or "",
            "address": params.get("direccion"),
            "phone": params.get("telefono"),
            "receiver_name": params.get("nombre_recibe"),
            "national_id": params.get("cedula") or None,
        },
        "payment_method": method,
    }
    res = await _call(chats, request, session_key, "order", body)
    if "error" in res:
        return res
    if not res.get("registered"):
        return {
            "registered": False,
            "order_id": None,
            "error_detail": res.get("error_detail"),
            "problems": res.get("problems") or [],
            "message": (
                "El pedido NO quedó registrado. Pasa el caso a un colega con escalate_to_human "
                "(reason_category=ORDER_REGISTRATION_FAILED, summary con el resumen del pedido) y dile al "
                "cliente que un colega le confirma en unos minutos."
            ),
        }
    out: dict[str, Any] = {
        "registered": True,
        "already_registered": bool(res.get("already_registered")),
        "order_id": res.get("order_id"),
        "order_reference": res.get("order_reference"),
        "payment_method": params.get("metodo_pago"),
        "currency": res.get("currency", "COP"),
        "subtotal_cop": res.get("subtotal_cop"),
        "items": res.get("items") or [],
        "portavelas_included": bool(res.get("portavelas_included")),
        "payment_instructions_sent": bool(res.get("payment_instructions_sent")),
    }
    if res.get("episode_closed"):
        # D1.10: para el hook de frontera de run_tool (lo saca antes de responder a Meta).
        out["_episode_closed"] = res["episode_closed"]
    if method == "cash_on_delivery":
        out["shipping_note"] = _SHIPPING_NOTE_COD
    else:
        out["shipping_cop"] = res.get("shipping_cop")
        out["total_cop"] = res.get("total_cop")
        out["shipping_is_minimum_rate"] = True
    out["message"] = (
        "Pedido registrado. Sigue el guion de cierre: un solo mensaje de despedida"
        + (
            " (este pedido INCLUYE portavelas: usa la despedida que lo menciona)"
            if out["portavelas_included"]
            else " (sin mencionar portavelas)"
        )
        + ". El equipo le envía al cliente las instrucciones de pago y verifica el pago; no etiquetes ni escales."
    )
    return out


async def manage_conversation_tag(
    chats: SessionActionCall,
    request: Request | None,
    *,
    session_key: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    tag = str(params.get("tag") or "").strip().upper()
    if tag not in TAGS:
        return {
            "error": "invalid_tag",
            "accepted": list(TAGS),
            "message": "Solo puedes proponer INTERESADO o RECHAZO. El estado de un pedido lo lleva el equipo de Hubara.",
        }
    res = await _call(
        chats, request, session_key, "tag", {"tag": tag, "motivo": params.get("motivo")}
    )
    if "error" in res:
        return res
    return {**res, "message": _tag_message(res)}


def _tag_message(res: dict[str, Any]) -> str:
    """Le dice al agente qué quedó aplicado (D1.3: Hubara decide). Nunca le pide
    volver a etiquetar: la reconciliación es determinista y se repetiría igual."""
    applied, proposed = str(res.get("tag") or ""), str(res.get("proposed_tag") or "")
    if res.get("reason") == "order_registered":
        return (
            f"Tu propuesta {proposed} no se aplicó: este cliente tiene un pedido registrado y su estado "
            f"({applied}) lo lleva el equipo de Hubara. No vuelvas a etiquetar esta conversación."
        )
    if res.get("reason") == "already_applied":
        return f"La conversación ya estaba etiquetada como {applied}; no hace falta volver a etiquetar."
    if res.get("reconciled"):
        return (
            f"Hubara aplicó {applied} en lugar de {proposed}: el cliente ya había dado datos de envío sin que el "
            "pedido quedara registrado, así que un colega tomará la conversación para completarlo. "
            "No vuelvas a etiquetar ni a responder en este chat."
        )
    return f"Etiqueta {applied} aplicada. No la menciones al cliente ni vuelvas a etiquetar esta conversación."


async def escalate_to_human(
    chats: SessionActionCall,
    request: Request | None,
    *,
    session_key: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    reason = str(params.get("reason_category") or "").strip().upper()
    if reason not in REASON_CATEGORIES:
        return {
            "error": "invalid_reason_category",
            "accepted": list(REASON_CATEGORIES),
            "message": "reason_category debe ser uno de los valores exactos de la tabla del skill de escalación.",
        }
    return await _call(
        chats,
        request,
        session_key,
        "escalate",
        {"reason_category": reason, "summary": params.get("summary")},
    )
