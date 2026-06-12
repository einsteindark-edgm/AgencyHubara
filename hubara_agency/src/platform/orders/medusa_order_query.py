"""MedusaOrderQuery — adapter live del OrderQueryPort contra Medusa v2.

Consulta `GET /admin/orders` + `GET /admin/draft-orders` y traduce las
respuestas a `OrderSummaryDTO` / `OrderDetailDTO` que el frontend Dashboard
consume.

R-DIP: este modulo NO importa de ningun agente. Es adapter del Protocol
`OrderQueryPort`.

Notas de mapeo (Medusa → Frontend):
  * `fulfillment_status` → `status` (con override por `payment_status` y
    `is_draft_order`).
  * `payment_status` → `pay_status`.
  * `metadata.payment_method` → `pay_type`. Default "confirmed".
  * `metadata.source == "hubara_whatsapp_sales"` → `channel = "WhatsApp"`.
    Default "Web".
  * `shipping_address.phone/city` → `phone/city`.
  * `created_at + 1 day` → `due_iso` (estimate operacional;
    `data_completeness_missing` lo marca como pendiente de integracion).
  * Hash deterministico del id → `color` ∈ {a..f}.
  * Primeras 2 letras del nombre del cliente → `short`.

Premortem fixes aplicados (ver docs/PREMORTEM_ORDERS.md):
  * **J4**: 401 → `error_detail` específico ("medusa_unauthorized") para
    que el operador distinga token expirado vs Medusa caído.
  * **A1**: `get()` acepta también el `display_id` ("#1247") buscando en
    `display_id` field — habilita deep-links sin requerir el backend id.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, cast

from src.platform.medusa.client import HttpMedusaClient, MedusaAPIError
from src.platform.medusa.settings import MedusaSettings
from src.platform.orders import display_id_cache
from src.platform.orders.query_port import (
    OrderAddressDTO,
    OrderDetailDTO,
    OrderItemDTO,
    OrderListDTO,
    OrderPayStatus,
    OrderPayType,
    OrderSummaryDTO,
    OrderTimelineEventDTO,
    OrderUiStatus,
)
from src.platform.orders.state import (  # noqa: F401 (re-exposed via module use)
    META_KEY_CANCELLED_REASON,
    META_KEY_HISTORY,
    META_KEY_HUMAN_NOTE,
    META_KEY_PAYMENT_CONFIRMED,
    META_KEY_SCHEDULED_DELIVERY_ISO,
    META_KEY_SCHEDULED_DELIVERY_TIME,
    META_KEY_STAGE,
    STAGE_VALUES,
    read_stage,
)

log = logging.getLogger(__name__)


# Slots que Medusa NO tiene hoy — la UI pinta marker "Datos pendientes
# de integración" en estos campos.
_MISSING_SLOTS_BASE: list[str] = [
    "agent",             # no se persiste el agente asignado
    "customer_history",  # estadisticas del cliente (LTV, etc.)
]

# Labels human-readable para cada stage. Usado para construir entries del
# timeline desde `hubara_stage_history`.
_STAGE_LABELS: dict[str, str] = {
    "new": "Pedido recibido",
    "preparing": "En preparación",
    "ready": "Pedido listo",
    "shipping": "En camino",
    "delivered": "Entregado",
    "cancelled": "Cancelado",
}

# Labels human-readable para `event` no-transitional del history.
_EVENT_LABELS: dict[str, str] = {
    "payment_confirmed": "Pago confirmado",
    "schedule_updated": "Reagendado",
}


class MedusaOrderQuery:
    """Adapter live del OrderQueryPort."""

    def __init__(
        self,
        client: HttpMedusaClient,
        settings: MedusaSettings,
    ) -> None:
        self._client = client
        self._settings = settings

    # ------------------------------------------------------------------
    # Public — OrderQueryPort
    # ------------------------------------------------------------------

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        include_drafts: bool = True,
    ) -> OrderListDTO:
        try:
            # Paralelizar las dos queries — orders + draft-orders viven en
            # endpoints separados de Medusa. Hacerlas secuenciales serializa
            # un round-trip evitable.
            tasks: list[Any] = [
                self._client.list_orders(limit=limit, offset=offset),
            ]
            if include_drafts:
                tasks.append(
                    self._client.list_draft_orders(limit=limit, offset=offset)
                )
            results = await asyncio.gather(*tasks, return_exceptions=False)

            orders_resp = results[0]
            drafts_resp = results[1] if include_drafts else {"draft_orders": [], "count": 0}

            summaries: list[OrderSummaryDTO] = []
            for raw in orders_resp.get("orders", []):
                summaries.append(self._build_summary(raw, is_draft=False))
                self._cache_display_id(raw)
            for raw in drafts_resp.get("draft_orders", []):
                summaries.append(self._build_summary(raw, is_draft=True))
                self._cache_display_id(raw)

            # Ordenar por created_at descendiente (mas reciente primero) —
            # los draft_orders se intercalan con orders por timestamp.
            summaries.sort(key=lambda s: s.created_at_ms, reverse=True)

            total_count = orders_resp.get("count", len(orders_resp.get("orders", [])))
            if include_drafts:
                total_count += drafts_resp.get(
                    "count", len(drafts_resp.get("draft_orders", []))
                )

            return OrderListDTO(
                orders=summaries,
                count=total_count,
                offset=offset,
                limit=limit,
                catalog_available=True,
                error_detail=None,
            )
        except MedusaAPIError as exc:
            # Premortem J4: distinguir 401 (token expirado) de 5xx (Medusa
            # caído) — el operador necesita saber cuál es para actuar.
            log.error(
                "MedusaOrderQuery.list failed",
                extra={
                    "status_code": exc.status_code,
                    "path": exc.path,
                    "body_preview": exc.body[:300],
                },
            )
            return OrderListDTO(
                orders=[],
                count=0,
                offset=offset,
                limit=limit,
                catalog_available=False,
                error_detail=_format_medusa_error(exc),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("MedusaOrderQuery.list unexpected failure")
            return OrderListDTO(
                orders=[],
                count=0,
                offset=offset,
                limit=limit,
                catalog_available=False,
                error_detail=f"unexpected_error: {type(exc).__name__}: {exc}",
            )

    async def get(self, order_id: str) -> OrderDetailDTO | None:
        """Get a single order by ID.

        Premortem A1: además del id nativo de Medusa (`order_01HXX...` /
        `draft_01HXX...`), aceptamos el `display_id` con o sin `#` ("#1247"
        o "1247"). Esto habilita deep-links del dashboard sin requerir que
        el frontend cache el backend id primero.
        """
        # Premortem A1: si entra un display_id (numérico, con o sin '#'),
        # hacemos un lookup primero contra /admin/orders + /admin/draft-orders
        # filtrando por `display_id`.
        normalized = order_id.lstrip("#")
        if normalized.isdigit():
            resolved_id = await self._resolve_display_id(normalized)
            if resolved_id is None:
                return None
            order_id = resolved_id

        # Bug fix 2026-05-26: en Medusa v2 los DRAFTS y ORDERS comparten el
        # mismo namespace de IDs (`order_*`) — NO hay un prefijo `draft_*`
        # confiable. Además `GET /admin/orders/{id}` devuelve drafts también
        # (con `status="draft"`), no solo orders reales. La heurística del
        # prefijo daba is_draft=False incorrectamente para drafts.
        #
        # Solución: usar `/admin/orders/{id}` como endpoint primario (porque
        # devuelve ambos tipos), y derivar `is_draft` del campo `status` del
        # payload — fuente de verdad de Medusa. Fallback a /admin/draft-orders
        # solo si /admin/orders responde 404.
        is_draft = False
        try:
            raw = await self._client.get_order(order_id)
            # Medusa v2 live: /admin/orders/{id} devuelve drafts también con
            # status="draft" (verificado 2026-05-26).
            is_draft = raw.get("status") == "draft"
        except MedusaAPIError as exc:
            if exc.status_code == 404:
                try:
                    raw = await self._client.get_draft_order(order_id)
                    # Si llegamos al fallback, sabemos con certeza que es draft.
                    is_draft = True
                except MedusaAPIError as exc2:
                    if exc2.status_code == 404:
                        return None
                    raise
            else:
                raise
        return self._build_detail(raw, is_draft=is_draft)

    @staticmethod
    def _cache_display_id(raw: dict[str, Any]) -> None:
        """Poblar el cache display→id desde filas de lista (L-2, gratis)."""
        did = raw.get("display_id")
        backend_id = raw.get("id")
        if did is not None and backend_id:
            display_id_cache.put(str(did), str(backend_id))

    async def _resolve_display_id(self, display_id_str: str) -> str | None:
        """Premortem A1: resolve `display_id` ("1247") → backend id
        ("order_01HXX..."). Cache first (el mapeo es inmutable y `list()` lo
        puebla); en miss, escanea `/admin/orders` + `/admin/draft-orders` EN
        PARALELO con `fields=id,display_id`. Precedencia: orders > drafts.

        L-2: en Railway esos list endpoints tardan 2-10s c/u con variabilidad
        alta aun con fields mínimos — el cache es lo que de verdad los evita.
        """
        cached = display_id_cache.get(display_id_str)
        if cached is not None:
            return cached

        # Algunos clients de Medusa no soportan filter por display_id
        # directamente — entonces hacemos page-scan limit=50, suficiente
        # para uso interactivo del dashboard.
        resolve_fields = "id,display_id"

        async def _scan_orders() -> str | None:
            page = await self._client.list_orders(
                limit=50, offset=0, fields=resolve_fields
            )
            for o in page.get("orders", []):
                if str(o.get("display_id")) == display_id_str:
                    return str(o["id"])
            return None

        async def _scan_drafts() -> str | None:
            page = await self._client.list_draft_orders(
                limit=50, offset=0, fields=resolve_fields
            )
            for o in page.get("draft_orders", []):
                if str(o.get("display_id")) == display_id_str:
                    return str(o["id"])
            return None

        results = await asyncio.gather(
            _scan_orders(), _scan_drafts(), return_exceptions=True
        )
        for kind, res in zip(("orders", "drafts"), results):
            if isinstance(res, BaseException):
                if isinstance(res, MedusaAPIError):
                    log.warning(
                        "_resolve_display_id: %s lookup failed (%s)", kind, res
                    )
                    continue
                raise res
            if res:
                display_id_cache.put(display_id_str, res)
                return res
        return None

    # ------------------------------------------------------------------
    # Internals — mapeo Medusa → DTOs
    # ------------------------------------------------------------------

    def _build_summary(
        self, raw: dict[str, Any], *, is_draft: bool
    ) -> OrderSummaryDTO:
        metadata = raw.get("metadata") or {}
        shipping_addr = raw.get("shipping_address") or {}
        customer = raw.get("customer") or {}

        # ---- nombre del cliente ----
        customer_name = _customer_name(customer, shipping_addr, raw.get("email"))

        # ---- channel ----
        source = (metadata.get("source") or "").lower()
        channel = "WhatsApp" if "whatsapp" in source else "Web"

        # ---- ids y formato del display_id ----
        order_id = str(raw["id"])
        display_id = raw.get("display_id")
        display = (
            f"#{display_id}" if isinstance(display_id, int) else f"#{order_id[-6:]}"
        )

        # ---- totales en COP integer ----
        total_cop = _to_int_cop(raw.get("total", 0))

        # ---- timestamps ----
        created_at_ms = _iso_to_ms(raw.get("created_at"))
        updated_at_ms = _iso_to_ms(raw.get("updated_at")) or created_at_ms

        # ---- status mapping ----
        # Priority chain (top wins):
        #
        #   0. Medusa hard-cancel (`status="canceled"` OR `canceled_at`) →
        #      "cancelled". Final state — sobre-escribe cualquier hubara_stage.
        #      Caso real: order #1 fue cancelada vía Medusa Admin (no via
        #      nuestro dashboard); tiene `hubara_stage_history` (de un
        #      payment_confirmed previo) pero NO `hubara_stage="cancelled"`.
        #      Sin esta priorización caía a "new" y aparecía en la columna
        #      equivocada del kanban.
        #
        #   1. `metadata.hubara_stage` explícito (canonical path) → usar.
        #      Esto incluye los soft-cancels via dashboard (que escriben
        #      `hubara_stage="cancelled"` sin tocar Medusa todavía).
        #
        #   2. `metadata.hubara_stage_history` presente (order tocada por
        #      el write side al menos una vez, ej. confirm_payment) →
        #      default "new" hasta que el humano transicione explícitamente.
        #
        #   3. Pre-feature legacy: no hay metadata `hubara_*` → derivar de
        #      payment_status + fulfillment_status (el `_map_status` legacy
        #      también detecta fulfillment_status="canceled", redundante
        #      con priority 0 pero defensivo).
        status: OrderUiStatus
        if raw.get("status") == "canceled" or raw.get("canceled_at"):
            status = "cancelled"
        elif metadata.get(META_KEY_STAGE) in STAGE_VALUES:
            status = read_stage(metadata)
        elif metadata.get(META_KEY_HISTORY):
            # Order ya escrita por write side, pero stage aún no transicionó.
            # Default "new" — el operador debe usar el dashboard para avanzar.
            status = "new"
        else:
            # Pre-feature: usar el cálculo legacy basado en payment+fulfillment.
            status = _map_status(
                fulfillment_status=raw.get("fulfillment_status"),
                payment_status=raw.get("payment_status"),
                is_draft=is_draft,
            )

        # ---- pay_status mapping ----
        # `hubara_payment_confirmed` flag (escrito por click "Confirmar pago"
        # del humano) trumpea el `payment_status` raw de Medusa — porque hoy
        # Medusa no tiene gateway integrado y reporta `not_paid` para todo.
        # Cuando integremos gateway, podemos volver a `_map_pay_status`.
        if metadata.get(META_KEY_PAYMENT_CONFIRMED) is True:
            pay_status: OrderPayStatus = "paid"
        else:
            pay_status = _map_pay_status(raw.get("payment_status"))
        pay_type: OrderPayType = (
            "cod" if metadata.get("payment_method") == "cash_on_delivery"
            else "confirmed"
        )

        # ---- items + pieces ----
        items_raw = raw.get("items") or []
        items_count = len(items_raw)
        pieces = sum(int(i.get("quantity", 0)) for i in items_raw)

        # ---- due date ----
        # Source of truth: `metadata.hubara_scheduled_delivery_iso` (escrito
        # por el operador al agendar). Si NO está agendada, devolvemos None —
        # NO inventamos un estimate (el comportamiento previo de "created_at
        # + 1d" se sentía como fecha real para el operador y confundía los
        # filtros "Hoy"/"Mañana", contando órdenes que en realidad no tenían
        # entrega agendada).
        scheduled_iso = metadata.get(META_KEY_SCHEDULED_DELIVERY_ISO)
        scheduled_time = metadata.get(META_KEY_SCHEDULED_DELIVERY_TIME)
        if isinstance(scheduled_iso, str) and scheduled_iso:
            due_iso = scheduled_iso
            due_time = scheduled_time if isinstance(scheduled_time, str) else None
        else:
            due_iso = None
            due_time = None

        # ---- overdue (premortem 2026-06-11, HIGH) ----
        # ÚNICA fuente del cálculo de reloj: el frontend ya NO recalcula sobre
        # datos cacheados (mapper puro, F0.5) — antes este campo era un
        # `False` hardcodeado "derivado client-side" y al purificar el mapper
        # la feature "Retrasadas" habría muerto en silencio (gotcha #1 del
        # repo: el schema lo permite ≠ el backend lo emite). Día calendario
        # UTC — espejo del compute viejo del frontend; el TODO de pasar a
        # America/Bogota vive en shared/lib/dates.ts y se decide junto.
        # Stages terminales NO cuentan: una orden entregada/cancelada no está
        # "retrasada" (mejora deliberada vs el cliente viejo, que marcaba
        # entregadas vencidas y ensuciaba el KPI).
        if due_iso is not None and status not in ("delivered", "cancelled"):
            today_utc = datetime.now(timezone.utc).date().isoformat()
            overdue = due_iso < today_utc
        else:
            overdue = False

        # ---- priority heuristic ----
        priority = "alta" if total_cop >= 200_000 else (
            "baja" if total_cop < 30_000 else "normal"
        )

        # ---- agent ----
        agent = metadata.get("agent") if isinstance(metadata.get("agent"), str) else "—"

        return OrderSummaryDTO(
            id=order_id,
            display_id=display,
            customer=customer_name,
            short=_initials(customer_name),
            color=_color_from_id(order_id),
            phone=shipping_addr.get("phone"),
            city=shipping_addr.get("city"),
            channel=channel,
            status=status,
            pay_status=pay_status,
            pay_type=pay_type,
            items=items_count,
            pieces=pieces,
            total_cop=total_cop,
            currency_code=raw.get("currency_code", "cop").upper(),
            is_draft=is_draft,
            due_iso=due_iso,
            due_time=due_time,
            overdue=overdue,
            priority=priority,
            agent=agent,
            created_at_ms=created_at_ms,
            updated_at_ms=updated_at_ms,
        )

    def _build_detail(
        self, raw: dict[str, Any], *, is_draft: bool
    ) -> OrderDetailDTO:
        summary = self._build_summary(raw, is_draft=is_draft)

        # Items con detalle.
        #
        # Heurística de suppression para mismatch (2026-05-26):
        # En esta tienda los aromas/colores viven como **tags** del producto,
        # no como variantes. Productos típicamente tienen 1 variante "Unico".
        # Para data persistida ANTES del fix del write side (en medusa_order.py),
        # `variant_label_mismatch=True` puede estar set falsamente. Si la
        # entry correspondiente en `metadata.variant_mismatches` indica que
        # el `selected_variant_title == "Unico"` (sentinel de single-variant),
        # suppress el flag — no era un mismatch real, solo un tag descriptivo.
        order_meta = raw.get("metadata") or {}
        order_mismatches = order_meta.get("variant_mismatches") or []
        single_variant_handles: set[str] = {
            str(m.get("handle"))
            for m in order_mismatches
            if isinstance(m, dict) and m.get("selected_variant_title") == "Unico"
        }

        items_detail = [
            OrderItemDTO(
                title=str(it.get("title", "—")),
                sku=it.get("sku") or it.get("variant_sku"),
                quantity=int(it.get("quantity", 0)),
                unit_price_cop=_to_int_cop(it.get("unit_price", 0)),
                total_cop=_to_int_cop(it.get("total", 0)) or (
                    _to_int_cop(it.get("unit_price", 0))
                    * int(it.get("quantity", 0))
                ),
                variant_label=(
                    (it.get("metadata") or {}).get("variant_label")
                    if isinstance(it.get("metadata"), dict) else None
                ),
                variant_label_mismatch=_compute_variant_mismatch(
                    it, single_variant_handles
                ),
                thumbnail=it.get("thumbnail"),
                handle=(
                    (it.get("metadata") or {}).get("handle")
                    if isinstance(it.get("metadata"), dict) else None
                ),
            )
            for it in (raw.get("items") or [])
        ]

        shipping = _map_address(raw.get("shipping_address"))
        billing = _map_address(raw.get("billing_address"))

        # Totales
        subtotal_cop = _to_int_cop(raw.get("subtotal", 0))
        shipping_cop = _to_int_cop(raw.get("shipping_total", 0))
        tax_total_cop = _to_int_cop(raw.get("tax_total", 0))
        discount_total_cop = _to_int_cop(raw.get("discount_total", 0))

        # ---- Timeline ----
        # Source primario: `hubara_stage_history` (entries del lifecycle real
        # — created → preparing → ready → ...).
        # Source fallback: created_at + transactions (para orders pre-feature).
        # Si Medusa tiene `canceled_at` y NO está reflejado en el history
        # (cancel hecho via Medusa Admin), inyectamos un evento sintético al
        # final del timeline — sin esto el inspector pinta vacío en cancels
        # externos.
        metadata = raw.get("metadata") or {}
        timeline = _build_timeline(
            stage_history=metadata.get(META_KEY_HISTORY),
            created_at_ms=summary.created_at_ms,
            is_draft=is_draft,
            transactions=raw.get("transactions") or [],
            currency_code=raw.get("currency_code", "cop"),
            canceled_at=raw.get("canceled_at"),
        )

        # ---- Payment method label ----
        pay_method_raw = metadata.get("payment_method")
        payment_method_label = {
            "transfer": "Transferencia",
            "card": "Tarjeta",
            "cash_on_delivery": "Contra entrega",
        }.get(pay_method_raw)

        # ---- Notes: combinamos human_note + cancelled_reason si aplica ----
        notes: list[str] = []
        human_note = metadata.get(META_KEY_HUMAN_NOTE)
        if isinstance(human_note, str) and human_note.strip():
            notes.append(human_note.strip())
        cancelled_reason = metadata.get(META_KEY_CANCELLED_REASON)
        if isinstance(cancelled_reason, str) and cancelled_reason.strip():
            notes.append(f"Motivo de cancelación: {cancelled_reason.strip()}")

        # ---- Data completeness missing ----
        missing = list(_MISSING_SLOTS_BASE)
        # Si no agendaron entrega (estado new sin scheduled), marker due_date.
        if not metadata.get(META_KEY_SCHEDULED_DELIVERY_ISO):
            missing.append("due_date")
        # Si no hay transactions, falta detalle de pago real (cuando integremos
        # gateway). El flag hubara_payment_confirmed cubre el caso "marcado
        # manualmente" pero el detalle del pago real lo tendremos vía Medusa.
        if (
            not raw.get("transactions")
            and not metadata.get(META_KEY_PAYMENT_CONFIRMED)
        ):
            missing.append("payment_method_detail")
        # Si no hay fulfillments, falta tracking
        if not raw.get("fulfillments"):
            missing.extend(["tracking_number", "shipping_provider"])

        return OrderDetailDTO(
            summary=summary,
            items_detail=items_detail,
            shipping_address=shipping,
            billing_address=billing,
            subtotal_cop=subtotal_cop,
            shipping_cop=shipping_cop,
            tax_total_cop=tax_total_cop,
            discount_total_cop=discount_total_cop,
            timeline=timeline,
            payment_method_label=payment_method_label,
            notes=notes,
            data_completeness_missing=missing,
        )


# ----------------------------------------------------------------------
# Helpers — puros, faciles de testear
# ----------------------------------------------------------------------


def _format_medusa_error(exc: MedusaAPIError) -> str:
    """Premortem J4: distinguir entre 401 (token expirado), 404, 5xx (down)
    para que el frontend pueda mostrar el mensaje correcto al operador.

    Cualquier 401 surface como `medusa_unauthorized` con hint claro — el
    operador sabe que tiene que rotar/refrescar el `MEDUSA_ADMIN_TOKEN`,
    NO esperar que Medusa "vuelva" (porque Medusa funciona, solo no nos
    autoriza).
    """
    if exc.status_code == 401:
        return (
            "medusa_unauthorized: HTTP 401 — el admin_token expiró o es "
            f"inválido. Verifica MEDUSA_ADMIN_TOKEN. ({exc.path})"
        )
    if exc.status_code == 403:
        return (
            f"medusa_forbidden: HTTP 403 — el token no tiene permisos para "
            f"{exc.path}. Verifica scopes del Secret API Key."
        )
    if 500 <= exc.status_code < 600:
        return f"medusa_unavailable: HTTP {exc.status_code} {exc.path}"
    return f"medusa_api_error: HTTP {exc.status_code} {exc.path}"


def _to_int_cop(value: Any) -> int:
    """Medusa v2 usa unidad MAYOR (49.99 = $49.99). COP no tiene fraccion,
    asi que un total de 17000 viene como 17000 (int) o 17000.0 (float).
    Convertimos a int seguro."""
    if value is None:
        return 0
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def _iso_to_ms(iso: Any) -> int:
    """ISO 8601 datetime → epoch ms. Medusa devuelve formato
    `2026-05-22T14:00:00.000Z`. Devuelve 0 si no parseable."""
    if not iso:
        return 0
    try:
        # Python 3.11+ acepta el "Z" sufijo directamente con fromisoformat
        # cuando reemplazamos al "+00:00".
        s = str(iso).replace("Z", "+00:00")
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    except (TypeError, ValueError):
        return 0


def _customer_name(
    customer: dict[str, Any] | None,
    shipping_addr: dict[str, Any] | None,
    email: str | None,
) -> str:
    customer = customer or {}
    shipping_addr = shipping_addr or {}
    full = " ".join(
        s for s in (
            customer.get("first_name") or shipping_addr.get("first_name"),
            customer.get("last_name") or shipping_addr.get("last_name"),
        ) if s
    ).strip()
    if full:
        return full
    if email:
        return email
    return "Cliente sin nombre"


def _initials(name: str) -> str:
    parts = [p for p in name.strip().split() if p]
    if not parts:
        return "—"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def _color_from_id(order_id: str) -> str:
    """Hash deterministico a una de las 6 paletas de la UI."""
    palette = ["a", "b", "c", "d", "e", "f"]
    # Sum of char codes mod len — estable y simple, no necesita hashlib.
    return palette[sum(ord(c) for c in order_id) % len(palette)]


def _map_status(
    *,
    fulfillment_status: Any,
    payment_status: Any,
    is_draft: bool,
) -> OrderUiStatus:
    """Mapea (payment_status, fulfillment_status, is_draft) → UI status enum.

    Reglas:
      * draft order sin captura → "new"
      * fulfillment=canceled → "cancelled"
      * fulfillment=fulfilled → "ready"
      * fulfillment=shipped o partially_shipped → "shipping"
      * fulfillment=delivered o partially_delivered → "delivered"
      * payment captured + fulfillment=not_fulfilled → "preparing"
      * otherwise → "new"
    """
    if fulfillment_status == "canceled" or payment_status == "canceled":
        return "cancelled"
    if fulfillment_status in ("partially_delivered", "delivered"):
        return "delivered"
    if fulfillment_status in ("partially_shipped", "shipped"):
        return "shipping"
    if fulfillment_status == "fulfilled":
        return "ready"
    if is_draft:
        return "new"
    if payment_status in ("captured", "partially_captured", "authorized", "partially_authorized"):
        return "preparing"
    return "new"


def _map_pay_status(payment_status: Any) -> OrderPayStatus:
    """Medusa payment_status → frontend payStatus."""
    if payment_status in ("captured", "authorized"):
        return "paid"
    if payment_status in ("partially_captured", "partially_authorized"):
        return "partial"
    if payment_status in ("refunded", "partially_refunded"):
        return "refund"
    return "pending"


def _map_address(raw: Any) -> OrderAddressDTO | None:
    if not isinstance(raw, dict):
        return None
    return OrderAddressDTO(
        first_name=raw.get("first_name"),
        last_name=raw.get("last_name"),
        phone=raw.get("phone"),
        address_1=raw.get("address_1"),
        address_2=raw.get("address_2"),
        city=raw.get("city"),
        country_code=raw.get("country_code"),
    )


def _compute_variant_mismatch(
    item: dict[str, Any], single_variant_handles: set[str]
) -> bool:
    """Lee `item.metadata.variant_label_mismatch` y aplica suppression para
    productos de variante única.

    Cuando el handle del item está en `single_variant_handles` (calculado
    desde `order.metadata.variant_mismatches[].selected_variant_title ==
    "Unico"`), forzamos `False` — el flag estaba puesto por la lógica vieja
    que ahora sabemos era ruidosa para esta tienda (aromas/colores son tags).
    """
    item_meta = item.get("metadata") or {}
    if not isinstance(item_meta, dict):
        return False
    handle = item_meta.get("handle")
    if isinstance(handle, str) and handle in single_variant_handles:
        return False
    return bool(item_meta.get("variant_label_mismatch"))


def _build_timeline(
    *,
    stage_history: Any,
    created_at_ms: int,
    is_draft: bool,
    transactions: list[Any],
    currency_code: str,
    canceled_at: Any = None,
) -> list[OrderTimelineEventDTO]:
    """Construye el timeline del inspector desde fuentes priorizadas:

    1. `hubara_stage_history` (metadata) — fuente canónica de transiciones.
       Mapea cada entry a un `OrderTimelineEventDTO` con label localizado.
    2. Si `stage_history` está vacío (orders pre-feature): fallback al
       timeline mínimo legacy (created + first transaction).
    3. Si Medusa tiene `canceled_at` y NO hay un evento "cancelled" en el
       timeline ya construido, añadimos un evento sintético. Necesario para
       cancels hechos via Medusa Admin (que NO actualizan nuestro
       `hubara_stage_history`).

    Para historias hubara, NO incluimos el `created_at` de Medusa como entry
    separado porque `from=null to=new` ya cubre ese momento.
    """
    timeline: list[OrderTimelineEventDTO] = []

    if isinstance(stage_history, list) and stage_history:
        for entry in stage_history:
            if not isinstance(entry, dict):
                continue
            ts_raw = entry.get("at_ms")
            if not isinstance(ts_raw, (int, float)):
                continue
            ts_ms = int(ts_raw)
            event_kind = entry.get("event")
            from_stage = entry.get("from")
            to_stage = entry.get("to")
            by = entry.get("by", "")
            note = entry.get("note")

            # 3 tipos de entries:
            #   * Transición de stage (from != to): "Pedido recibido", etc.
            #   * Evento no-transicional (event="payment_confirmed"): label especial.
            #   * Edit de metadata sin transición (from == to): solo si tiene event.
            if event_kind:
                label = _EVENT_LABELS.get(event_kind, event_kind)
                event_type = event_kind
            elif from_stage != to_stage and to_stage in _STAGE_LABELS:
                label = _STAGE_LABELS[to_stage]
                event_type = f"stage:{to_stage}"
            else:
                # No-op entry — saltamos para no llenar el timeline de ruido.
                continue

            detail_parts = []
            if by:
                detail_parts.append(f"por {by}")
            if note:
                detail_parts.append(note)
            detail = " · ".join(detail_parts) if detail_parts else None

            timeline.append(
                OrderTimelineEventDTO(
                    type=event_type,
                    label=label,
                    timestamp_ms=ts_ms,
                    detail=detail,
                )
            )
        # Si construimos timeline desde history, no agregamos el fallback
        # legacy — ya tenemos la info canónica.
        # Pero si Medusa fue cancelado externamente (admin Medusa) y el
        # history NO refleja la cancelación, inyectamos el evento.
        _maybe_append_canceled_event(timeline, canceled_at)
        return timeline

    # Fallback legacy para orders pre-feature (sin hubara_stage_history).
    if created_at_ms:
        timeline.append(
            OrderTimelineEventDTO(
                type="created",
                label="Pedido creado" + (" (draft)" if is_draft else ""),
                timestamp_ms=created_at_ms,
                detail=None,
            )
        )
    for tx in transactions:
        if not isinstance(tx, dict):
            continue
        ts = _iso_to_ms(tx.get("created_at"))
        if not ts:
            continue
        timeline.append(
            OrderTimelineEventDTO(
                type="payment_captured",
                label="Pago capturado",
                timestamp_ms=ts,
                detail=f"{_to_int_cop(tx.get('amount', 0)):,} {currency_code.upper()}".replace(",", "."),
            )
        )
        break
    # Igual que en la rama de history: añadir evento "Cancelado" si Medusa
    # marcó la order como canceled (sea via Admin o nuestro cancel hard).
    _maybe_append_canceled_event(timeline, canceled_at)
    return timeline


def _maybe_append_canceled_event(
    timeline: list[OrderTimelineEventDTO], canceled_at: Any
) -> None:
    """Añade un evento "Cancelado" al timeline si Medusa tiene `canceled_at`
    pero el timeline aún no incluye una transición a `cancelled` (caso típico:
    cancelación hecha via Medusa Admin sin pasar por nuestro write side)."""
    if not canceled_at:
        return
    ts_ms = _iso_to_ms(canceled_at)
    if not ts_ms:
        return
    # ¿Ya hay un evento de cancelación? evita duplicados.
    already_cancelled = any(
        e.type == "stage:cancelled" or e.type == "cancelled"
        for e in timeline
    )
    if already_cancelled:
        return
    timeline.append(
        OrderTimelineEventDTO(
            type="stage:cancelled",
            label=_STAGE_LABELS["cancelled"],
            timestamp_ms=ts_ms,
            detail="vía Medusa Admin",
        )
    )


__all__ = [
    "MedusaOrderQuery",
    # Helpers exportados solo para tests
    "_map_status",
    "_map_pay_status",
    "_initials",
    "_color_from_id",
    "_to_int_cop",
    "_iso_to_ms",
    "_build_timeline",
]


# Silenciar advertencia de unused import (sin esto ruff podria comer cast)
_ = cast
