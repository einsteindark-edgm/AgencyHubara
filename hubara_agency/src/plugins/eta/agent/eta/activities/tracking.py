"""Activities de tracking del sub-agente ETA (notificador puro).

Manejan el estado per-sesión del seguimiento (``metadata.eta_tracking``) y la
lectura de los datos vivos del pedido. Todas son I/O puras (R-STATELESS): el
estado durable vive en ``metadata.json`` del vault, no en módulo.

**Cambio de comportamiento (2026-06-10, "convivencia ETA/Sales")**: el ETA ya
NO posee la conversación. ``start_eta_tracking`` NO toca ``active_route`` ni
``tag`` — el turno conversacional es SIEMPRE de Sales (o humano); el ETA solo
empuja notificaciones de estado. Antes ``start`` pisaba la ruta (incluso
``humano`` — bug: el claim respetaba humano pero el start no) y secuestraba la
conversación entera mientras el pedido estuviera en tránsito.

**Multi-pedido**: un cliente puede tener N pedidos en tránsito a la vez. El
tracking es un mapa por ``order_id`` (antes era UNO solo y un pedido nuevo
reseteaba el anterior, descartando sus notificaciones como "stale").

``metadata.eta_tracking`` shape (v2)::

    {
      "orders": {
        "order_01...": {
          "order_id": "order_01...",
          "current_stage": "shipping",
          "notified_stages": ["preparing", "ready", "shipping"],
          "events": [
            {"stage": "preparing", "agent_msg": "...", "at_ms": 1779...,
             "reply": null, "flagged": false, "flag": null},
            ...
          ],
          "started_at_ms": 1779...
        }
      }
    }

El shape v1 (un solo pedido top-level, con ``order_id`` en la raíz) se migra
on-read con ``_orders_map`` y se persiste como v2 en la próxima escritura.

Es la **fuente del timeline** que la dashboard API (``eta/api``) lee
para pintar la sección ETA del frontend.
"""
from __future__ import annotations

import time
from typing import Any

from temporalio import activity

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.state import FilesystemMetadataStore
from src.platform.whatsapp.window import is_in_service_window


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


# El customer de ventas WhatsApp se crea en Medusa con un nombre placeholder
# (``first_name="Cliente"``, ``last_name="WhatsApp"`` — ver
# ``medusa_order._upsert_customer``). Sin este filtro, TODA notificación
# saludaría "¡Hola Cliente!". Preferimos "¡Hola!" a secas antes que un nombre
# falso: devolvemos "" y el prompt/plantilla saludan sin nombre.
_PLACEHOLDER_NAMES = {"cliente whatsapp", "cliente sin nombre", "cliente"}


def _display_first_name(full_name: str | None) -> str:
    """Primer nombre para el saludo, o "" si es el placeholder de Medusa."""
    if not full_name:
        return ""
    if full_name.strip().lower() in _PLACEHOLDER_NAMES:
        return ""
    return _first_name(full_name)


def _format_cop(total_cop: int | None) -> str:
    """Formatea un monto COP al estilo del mock: ``$ 215.000`` (miles con punto)."""
    if not total_cop:
        return ""
    return "$ " + f"{int(total_cop):,}".replace(",", ".")


def _empty_entry(order_id: str) -> dict[str, Any]:
    return {
        "order_id": order_id,
        "current_stage": None,
        "notified_stages": [],
        "events": [],
        "started_at_ms": int(time.time() * 1000),
    }


def _orders_map(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Mapa ``order_id → tracking entry`` desde ``metadata.eta_tracking``.

    Acepta el shape v2 (``{"orders": {...}}``) y migra on-read el v1 legacy
    (un solo pedido con ``order_id`` en la raíz). NO escribe — la migración
    persiste cuando la activity que llamó escriba el mapa de vuelta.
    """
    tracking = data.get("eta_tracking")
    if not isinstance(tracking, dict):
        return {}
    orders = tracking.get("orders")
    if isinstance(orders, dict):
        return {str(k): dict(v) for k, v in orders.items() if isinstance(v, dict)}
    legacy_id = tracking.get("order_id")
    if legacy_id:
        entry = dict(tracking)
        return {str(legacy_id): entry}
    return {}


def _write_orders_map(
    store: FilesystemMetadataStore,
    session_id: str,
    data: dict[str, Any],
    orders: dict[str, dict[str, Any]],
) -> None:
    data["eta_tracking"] = {"orders": orders}
    store.write(session_id, data)



def _items_label(detail: object) -> str:
    """Resumen humano de los productos del pedido: "2× Vela Cruz de Vida,
    1× Vela Sándalo" (máx 3, luego "y N más"). El cliente no sabe qué es
    "#6" — el mensaje SIEMPRE nombra qué se está moviendo."""
    items = getattr(detail, "items_detail", None) or []
    parts: list[str] = []
    for it in items[:3]:
        title = getattr(it, "title", "") or ""
        qty = getattr(it, "quantity", 0) or 0
        if title:
            parts.append(f"{qty}× {title}" if qty > 1 else title)
    if len(items) > 3:
        parts.append(f"y {len(items) - 3} más")
    return ", ".join(parts)

@activity.defn(name="start_eta_tracking_activity")
async def start_eta_tracking_activity(session_id: str, order_id: str) -> None:
    """Inicializa (idempotente) el tracking del pedido en el mapa multi-pedido.

    NO toca ``active_route`` ni ``tag``: el ETA es un notificador puro — el
    turno conversacional es de Sales (o humano) y las notificaciones se
    intercalan en el hilo sin robarlo. (El diseño anterior seteaba
    ``active_route=eta`` + ``tag=ETA`` acá, pisando incluso ``humano``.)
    """
    store = _store()
    data = _safe_read(store, session_id)
    orders = _orders_map(data)
    if order_id not in orders:
        orders[order_id] = _empty_entry(order_id)
    _write_orders_map(store, session_id, data, orders)
    activity.logger.info(
        "start_eta_tracking_activity: session=%s order=%s (tracked=%d)",
        session_id, order_id, len(orders),
    )


@activity.defn(name="claim_eta_notification_activity")
async def claim_eta_notification_activity(
    session_id: str, order_id: str, stage: str
) -> dict[str, Any] | None:
    """Decide si corresponde notificar este cambio de estado y, si sí, devuelve
    los datos vivos del pedido para rellenar el mensaje.

    Devuelve ``None`` (saltar la notificación) SOLO cuando el stage ya está en
    ``notified_stages`` DE ESE pedido — dedup ante eventos duplicados.
    (Multi-pedido: cada ``order_id`` tiene su tracking; un pedido sin entry la
    crea acá — p.ej. entró directo en ``ready``.)

    ``active_route == humano`` NO bloquea (L-6, run 19ee6679): el guard venía
    del modelo viejo donde notificar = tomar el turno conversacional. Hoy la
    notificación es push informativo puro — y como TODA venta exitosa termina
    con ``route=humano`` (verificación de pago, terminal por diseño), el guard
    bloqueaba las notificaciones de TODOS los pedidos vendidos.

    Si corresponde, fetchea los datos del pedido del order query port (platform,
    R-DIP OK) y los devuelve como slots JSON-safe. NO reserva el stage acá: la
    reserva (``notified_stages``) ocurre en ``record_eta_notification_activity``
    tras un envío exitoso, para no quemar el stage si el LLM/envío falla.
    """
    store = _store()
    data = _safe_read(store, session_id)

    orders = _orders_map(data)
    entry = orders.get(order_id)
    if entry is None:
        # Pedido sin tracking previo (stage inicial distinto de `preparing`,
        # p.ej. movido directo a `ready`): lo damos de alta acá.
        entry = _empty_entry(order_id)
        orders[order_id] = entry
        _write_orders_map(store, session_id, data, orders)

    if stage in (entry.get("notified_stages") or []):
        activity.logger.info(
            "claim_eta_notification: session=%s order=%s stage=%s ya notificado "
            "— dedup", session_id, order_id, stage,
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
        # con lo mínimo: número = order_id, sin monto. Sin datos del pedido NO
        # sabemos si el pago está confirmado → `payment_confirmed=False` para que
        # el mensaje NO afirme un pago que no podemos verificar.
        return {
            "customer_name": "",
            "order_display_id": order_id,
            "total_label": "",
            "pay_type": "confirmed",
            "payment_confirmed": False,
            "delivery_window": None,
            "items_label": "",
            "in_service_window": in_window,
        }

    summary = detail.summary
    return {
        "customer_name": _display_first_name(summary.customer),
        "order_display_id": summary.display_id,
        "total_label": _format_cop(summary.total_cop),
        "pay_type": summary.pay_type,
        # `pay_type` es solo la MODALIDAD (cod vs prepago) y defaultea a
        # "confirmed" cuando falta `payment_method` — NO dice si el cliente ya
        # pagó. El pago real lo confirma el flag humano "Confirmar pago", que
        # llega como `pay_status == "paid"`. Pasamos ese booleano honesto para
        # que la notificación NO mienta ("tu pago ya está confirmado") en pedidos
        # nuevos/en preparación cuyo pago aún no se confirmó.
        "payment_confirmed": getattr(summary, "pay_status", None) == "paid",
        # v1: mensaje genérico — sin transportadora/guía/ventana específica
        # (el backend no las modela todavía). El slot queda para una HU futura.
        "delivery_window": None,
        "items_label": _items_label(detail),
        "in_service_window": in_window,
    }


@activity.defn(name="record_eta_notification_activity")
async def record_eta_notification_activity(
    session_id: str, order_id: str, stage: str, agent_msg: str
) -> None:
    """Persiste la notificación enviada en el timeline + reserva el stage.

    Appendea un ``TrackedEvent`` a ``eta_tracking.events`` (lo que la dashboard
    API sirve al frontend) y agrega el stage a ``notified_stages`` (dedup
    durable que sobrevive a continue-as-new / replace).
    """
    store = _store()
    data = _safe_read(store, session_id)
    orders = _orders_map(data)
    entry = orders.get(order_id) or _empty_entry(order_id)

    notified = list(entry.get("notified_stages") or [])
    if stage not in notified:
        notified.append(stage)
    entry["notified_stages"] = notified
    entry["current_stage"] = stage

    events = list(entry.get("events") or [])
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
    entry["events"] = events
    orders[order_id] = entry
    _write_orders_map(store, session_id, data, orders)


_TERMINAL_STAGES = {"delivered", "cancelled"}


@activity.defn(name="all_trackings_terminal_activity")
async def all_trackings_terminal_activity(session_id: str) -> bool:
    """True si TODOS los pedidos trackeados están en estado terminal
    (delivered/cancelled) y hay al menos uno. El workflow lo usa para
    cerrarse proactivamente en vez de dormir el idle de 7 días — revivir
    es gratis (signal_with_start, L-8)."""
    store = _store()
    data = _safe_read(store, session_id)
    orders = _orders_map(data)
    if not orders:
        return False
    return all(
        (e.get("current_stage") or "") in _TERMINAL_STAGES
        for e in orders.values()
    )
