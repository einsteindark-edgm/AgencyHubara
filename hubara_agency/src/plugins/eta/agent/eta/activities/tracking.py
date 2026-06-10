"""Activities de tracking del sub-agente ETA.

Manejan el estado per-sesión del seguimiento (``metadata.eta_tracking``) y la
lectura de los datos vivos del pedido. Todas son I/O puras (R-STATELESS): el
estado durable vive en ``metadata.json`` del vault, no en módulo.

``metadata.eta_tracking`` shape::

    {
      "order_id": "order_01...",
      "current_stage": "shipping",
      "notified_stages": ["preparing", "ready", "shipping"],
      "events": [
        {"stage": "preparing", "agent_msg": "...", "at_ms": 1779...,
         "reply": "ok 👍", "flagged": false, "flag": null},
        ...
      ],
      "started_at_ms": 1779...
    }

Es la **fuente del timeline** que la dashboard API (``eta/api``) lee
para pintar la sección ETA del frontend.
"""
from __future__ import annotations

import time
from typing import Any

from temporalio import activity

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.constants import ROUTE_HUMANO
from src.platform.plugin_manifest import get_worker_spec
from src.platform.state import FilesystemMetadataStore
from src.platform.whatsapp.window import is_in_service_window

# F6 (route registry): la ruta que ESTE agente posee se declara en SU manifest
# (`agent.workers[eta].owns_route`) — única fuente de verdad. Pre-F6 era la
# constante `ROUTE_ETA` en platform/constants.py (spinal PROTECTED): un agente
# nuevo con ruta propia tenía que editar un archivo central (violación INV-1).
_OWN_ROUTE: str = str(get_worker_spec("eta", "eta").get("owns_route") or "eta")


def _store() -> FilesystemMetadataStore:
    return FilesystemMetadataStore(WORKSPACE_VAULT_DIR)


def _safe_read(store: FilesystemMetadataStore, session_id: str) -> dict[str, Any]:
    try:
        data = store.read(session_id)
    except Exception:  # noqa: BLE001 — vault puede no existir aún
        return {}
    return data if isinstance(data, dict) else {}


def _first_name(full_name: str | None) -> str:
    """Primer token del nombre completo (el wording del mock usa el nombre de pila)."""
    if not full_name:
        return ""
    return full_name.strip().split()[0] if full_name.strip() else ""


def _format_cop(total_cop: int | None) -> str:
    """Formatea un monto COP al estilo del mock: ``$ 215.000`` (miles con punto)."""
    if not total_cop:
        return ""
    return "$ " + f"{int(total_cop):,}".replace(",", ".")


@activity.defn(name="start_eta_tracking_activity")
async def start_eta_tracking_activity(session_id: str, order_id: str) -> None:
    """Marca la conversación como propiedad del Agente ETA e inicializa el
    estado de tracking. Idempotente.

    Setea ``active_route=eta`` + ``tag=ETA`` JUNTOS (mantiene el invariante
    route↔tag: la conversación sale de la bandeja humana y entra a tracking ETA).
    Si ya había ``eta_tracking`` para OTRO ``order_id``, lo resetea — un pedido
    nuevo entró en preparación, empieza un seguimiento fresco.
    """
    store = _store()
    data = _safe_read(store, session_id)
    data["active_route"] = _OWN_ROUTE
    data["tag"] = "ETA"

    tracking = data.get("eta_tracking")
    if not isinstance(tracking, dict) or tracking.get("order_id") != order_id:
        tracking = {
            "order_id": order_id,
            "current_stage": None,
            "notified_stages": [],
            "events": [],
            "started_at_ms": int(time.time() * 1000),
        }
    data["eta_tracking"] = tracking
    store.write(session_id, data)
    activity.logger.info(
        "start_eta_tracking_activity: session=%s order=%s", session_id, order_id
    )


@activity.defn(name="claim_eta_notification_activity")
async def claim_eta_notification_activity(
    session_id: str, order_id: str, stage: str
) -> dict[str, Any] | None:
    """Decide si corresponde notificar este cambio de estado y, si sí, devuelve
    los datos vivos del pedido para rellenar el mensaje.

    Devuelve ``None`` (saltar la notificación) cuando:
      * ``active_route == humano`` — un humano tomó la conversación (p.ej. ETA
        ya escaló una queja); no pisamos su turno con una notificación automática.
      * el tracking activo es de OTRO ``order_id`` — la sesión sigue un pedido
        distinto (un pedido nuevo reemplazó al anterior); este stage es stale.
      * el stage ya está en ``notified_stages`` — dedup ante eventos duplicados.

    Si corresponde, fetchea los datos del pedido del order query port (platform,
    R-DIP OK) y los devuelve como slots JSON-safe. NO reserva el stage acá: la
    reserva (``notified_stages``) ocurre en ``record_eta_notification_activity``
    tras un envío exitoso, para no quemar el stage si el LLM/envío falla.
    """
    store = _store()
    data = _safe_read(store, session_id)

    if data.get("active_route") == ROUTE_HUMANO:
        activity.logger.info(
            "claim_eta_notification: session=%s en ruta humano — skip stage=%s",
            session_id, stage,
        )
        return None

    tracking = data.get("eta_tracking")
    if not isinstance(tracking, dict) or tracking.get("order_id") != order_id:
        activity.logger.info(
            "claim_eta_notification: session=%s tracking no coincide con order=%s "
            "— skip stage=%s", session_id, order_id, stage,
        )
        return None

    if stage in (tracking.get("notified_stages") or []):
        activity.logger.info(
            "claim_eta_notification: session=%s stage=%s ya notificado — dedup",
            session_id, stage,
        )
        return None

    # Ventana de servicio 24h de WhatsApp: DENTRO → el agente puede mandar texto
    # libre (LLM); FUERA → Meta SOLO permite un template de utilidad aprobado.
    # Un pedido suele tardar días entre preparación y entrega, así que la ventana
    # casi siempre está cerrada cuando llega la notificación → el workflow usará
    # el template `order_status_utility_v1`. La decisión la toma el workflow con
    # este flag (no podemos leer metadata en el workflow — R-DET).
    in_window = is_in_service_window(int(time.time() * 1000), data)

    # Datos vivos del pedido (platform port — R-DIP: chats → platform).
    # Tolerante a Medusa caído/sin configurar: si falla, notificamos con lo
    # mínimo (order_id como referencia) en vez de perder la notificación.
    detail = None
    try:
        from src.platform.orders.composition import get_order_query_port

        detail = await get_order_query_port().get(order_id)
    except Exception:  # noqa: BLE001 — Medusa caído / sin configurar
        activity.logger.warning(
            "claim_eta_notification: order query port no disponible para %s — "
            "notifico con datos mínimos",
            order_id,
        )

    if detail is None:
        # Pedido no resoluble (stub / borrado / Medusa caído). Igual notificamos
        # con lo mínimo: número = order_id, sin monto.
        return {
            "customer_name": "",
            "order_display_id": order_id,
            "total_label": "",
            "pay_type": "confirmed",
            "delivery_window": None,
            "in_service_window": in_window,
        }

    summary = detail.summary
    return {
        "customer_name": _first_name(summary.customer),
        "order_display_id": summary.display_id,
        "total_label": _format_cop(summary.total_cop),
        "pay_type": summary.pay_type,
        # v1: mensaje genérico — sin transportadora/guía/ventana específica
        # (el backend no las modela todavía). El slot queda para una HU futura.
        "delivery_window": None,
        "in_service_window": in_window,
    }


@activity.defn(name="record_eta_notification_activity")
async def record_eta_notification_activity(
    session_id: str, stage: str, agent_msg: str
) -> None:
    """Persiste la notificación enviada en el timeline + reserva el stage.

    Appendea un ``TrackedEvent`` a ``eta_tracking.events`` (lo que la dashboard
    API sirve al frontend) y agrega el stage a ``notified_stages`` (dedup
    durable que sobrevive a continue-as-new / replace).
    """
    store = _store()
    data = _safe_read(store, session_id)
    tracking = data.get("eta_tracking")
    if not isinstance(tracking, dict):
        # Defensivo: el bootstrap debería haber inicializado el tracking.
        tracking = {
            "order_id": "",
            "current_stage": None,
            "notified_stages": [],
            "events": [],
            "started_at_ms": int(time.time() * 1000),
        }

    notified = list(tracking.get("notified_stages") or [])
    if stage not in notified:
        notified.append(stage)
    tracking["notified_stages"] = notified
    tracking["current_stage"] = stage

    events = list(tracking.get("events") or [])
    events.append(
        {
            "stage": stage,
            "agent_msg": agent_msg,
            "at_ms": int(time.time() * 1000),
            "reply": None,
            "flagged": False,
            "flag": None,
        }
    )
    tracking["events"] = events
    data["eta_tracking"] = tracking
    store.write(session_id, data)


@activity.defn(name="record_eta_reply_activity")
async def record_eta_reply_activity(
    session_id: str, reply: str, flagged: bool, flag: str | None
) -> None:
    """Adjunta la respuesta del cliente al último evento del timeline.

    El mock muestra la respuesta del cliente colgada del stage al que responde
    (``reply``), y marca ``flagged`` + ``flag`` cuando la pregunta se salió del
    alcance del agente y se escaló a humano. Si todavía no hay ningún evento
    (el cliente escribió antes de cualquier notificación), crea uno mínimo.
    """
    store = _store()
    data = _safe_read(store, session_id)
    tracking = data.get("eta_tracking")
    if not isinstance(tracking, dict):
        return  # sin tracking activo — nada que anotar (no debería pasar)

    events = list(tracking.get("events") or [])
    if not events:
        events.append(
            {
                "stage": tracking.get("current_stage"),
                "agent_msg": "",
                "at_ms": int(time.time() * 1000),
                "reply": reply,
                "flagged": flagged,
                "flag": flag,
            }
        )
    else:
        last = dict(events[-1])
        last["reply"] = reply
        last["flagged"] = flagged
        last["flag"] = flag
        events[-1] = last
    tracking["events"] = events
    data["eta_tracking"] = tracking
    store.write(session_id, data)
