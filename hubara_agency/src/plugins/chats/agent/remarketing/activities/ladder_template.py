"""Toque por PLANTILLA de la escalera de reactivación (CSW cerrada).

Fuera de la ventana de 24h Meta solo acepta plantillas aprobadas. La central
(`decide_reengagement`) ya lo sabe y devuelve `channel="template"`, pero el
RemarketingWorkflow lo ignoraba y corría el LLM free-form (runs
`01a0b0da`…`01a0b586`: el último intento ya venía con `channel: template`).

La elección es determinista (sin LLM — el copy vive aprobado en Meta):
  * hay pedido a medias → `cart_recovery_marketing_v2` ("quedó X en tu carrito");
  * si no → `followup_interest_marketing_v1` (seguimiento genérico, sin
    variables: nada que inventar sobre un cliente que no eligió producto).

Regla del operador "no perder dinero": las plantillas de la escalera son de
MARKETING, así que salen SOLO cuando son gratis (ventana de 72h del anuncio).
Excepción: la CITA del cliente ("les escribo la otra semana") sale ese día
aunque sea pagada — UN toque con el seguimiento genérico.
Fuera de ella la central puede recomendar una `utility` paga (gancho
transaccional) — 15× más barata que lo que mandaríamos acá — y ese seguimiento
ya tiene dueño (watchdog / ETA / verificación humana del pago): la escalera
consume el peldaño sin enviar.

Devuelve un string plano (R-JSON): `"sent"` o `"failed:<detalle>"`. NUNCA
levanta: una plantilla pausada / sin aprobar / con el tope diario del usuario
(131049) no debe colgar el workflow en reintentos — el peldaño se consume
como `failed` y la escalera sigue.
"""
from __future__ import annotations

from typing import Any

from temporalio import activity

from src.sdk.messagingkit import (
    appointment_pending,
    lead_state_from_metadata,
    send_template_to_session,
)
from src.sdk.runtime import with_heartbeat

TEMPLATE_FOLLOWUP = "followup_interest_marketing_v1"
TEMPLATE_CART = "cart_recovery_marketing_v2"

_PRODUCT_LABEL_MAX = 120


def choose_ladder_template(
    metadata: dict[str, Any], *, is_free: bool
) -> tuple[str, dict[str, str]] | None:
    """metadata → (template_name, variables), o None si no corresponde. Pura."""
    if appointment_pending(metadata):
        # CITA: el cliente dijo cuándo retoma ("les escribo la otra semana").
        # Decisión del operador (2026-09-22): el seguimiento genérico, aunque
        # sea pagado — es UN toque que el cliente mismo pidió.
        return TEMPLATE_FOLLOWUP, {}
    if not is_free:
        return None  # marketing pago: no se pierde dinero en quien no contestó
    lead = lead_state_from_metadata(metadata)
    if lead.has_order_draft:
        episodes = metadata.get("episodes") or []
        slots = ((episodes[-1] if episodes else {}).get("order_draft") or {}).get(
            "slots"
        ) or {}
        label = str(slots.get("producto") or slots.get("product") or "").strip()
        if label:
            return TEMPLATE_CART, {"product_label": label[:_PRODUCT_LABEL_MAX]}
    if lead.transactional_hook and not lead.has_order_draft:
        # Pedido registrado / pago pendiente: "lo que estabas mirando" sería
        # absurdo — su seguimiento es de ETA / del humano que verifica el pago.
        return None
    return TEMPLATE_FOLLOWUP, {}


@activity.defn(name="send_remarketing_template_activity")
@with_heartbeat(every=10)
async def send_remarketing_template_activity(session_id: str, is_free: bool) -> str:
    import json
    from pathlib import Path

    # Import local: se resuelve al CALL time para que `_isolate_vault_dir`
    # pueda re-bindear `src.sdk.runtime` en tests.
    from src.sdk.runtime import WORKSPACE_VAULT_DIR

    try:
        metadata = json.loads(
            (Path(WORKSPACE_VAULT_DIR) / session_id / "metadata.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError):
        return "failed:metadata_unreadable"

    choice = choose_ladder_template(metadata, is_free=is_free)
    if choice is None:
        return "failed:no_applicable_free_template"
    template_name, variables = choice
    try:
        result = await send_template_to_session(session_id, template_name, variables)
    except Exception as exc:  # noqa: BLE001 — ver docstring: nunca levanta
        activity.logger.warning(
            "remarketing ladder: plantilla %s falló para %s: %s",
            template_name,
            session_id,
            exc,
        )
        return f"failed:{type(exc).__name__}"
    return "sent" if result.ok else f"failed:{result.error}"
