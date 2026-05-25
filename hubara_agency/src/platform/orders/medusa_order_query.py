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
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from src.platform.medusa.client import HttpMedusaClient, MedusaAPIError
from src.platform.medusa.settings import MedusaSettings
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

log = logging.getLogger(__name__)


# Slots que Medusa NO tiene hoy — la UI pinta marker "Datos pendientes
# de integración" en estos campos.
_MISSING_SLOTS_BASE: list[str] = [
    "due_date",          # no tracking de fecha de entrega comprometida
    "agent",             # no se persiste el agente asignado
    "priority",          # derivado heuristicamente del total
    "notes",             # notas internas no estan en Medusa todavia
    "customer_history",  # estadisticas del cliente (LTV, etc.)
]


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
            for raw in drafts_resp.get("draft_orders", []):
                summaries.append(self._build_summary(raw, is_draft=True))

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

        # Heuristica simple: si el id empieza con `draft_` → draft endpoint.
        # Si no — orders endpoint. Si falla con 404 caemos al otro endpoint
        # como fallback (Medusa puede haber cambiado el prefijo en otra
        # version, asi no rompemos).
        is_draft = order_id.startswith("draft_")
        try:
            raw = (
                await self._client.get_draft_order(order_id)
                if is_draft
                else await self._client.get_order(order_id)
            )
        except MedusaAPIError as exc:
            if exc.status_code == 404:
                # Fallback al otro endpoint por si la heuristica fallo.
                try:
                    raw = (
                        await self._client.get_order(order_id)
                        if is_draft
                        else await self._client.get_draft_order(order_id)
                    )
                    is_draft = not is_draft
                except MedusaAPIError as exc2:
                    if exc2.status_code == 404:
                        return None
                    raise
            else:
                raise
        return self._build_detail(raw, is_draft=is_draft)

    async def _resolve_display_id(self, display_id_str: str) -> str | None:
        """Premortem A1: resolve `display_id` ("1247") → backend id
        ("order_01HXX..."). Probamos primero `/admin/orders?display_id=N`,
        después `/admin/draft-orders`.
        """
        # Algunos clients de Medusa no soportan filter por display_id
        # directamente — entonces hacemos page-scan limit=50, suficiente
        # para uso interactivo del dashboard.
        try:
            page = await self._client.list_orders(limit=50, offset=0)
            for o in page.get("orders", []):
                if str(o.get("display_id")) == display_id_str:
                    return str(o["id"])
        except MedusaAPIError as exc:
            log.warning(
                "_resolve_display_id: orders lookup failed (%s) — trying drafts.",
                exc,
            )
        try:
            page = await self._client.list_draft_orders(limit=50, offset=0)
            for o in page.get("draft_orders", []):
                if str(o.get("display_id")) == display_id_str:
                    return str(o["id"])
        except MedusaAPIError as exc:
            log.warning("_resolve_display_id: draft_orders lookup failed (%s)", exc)
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
        status = _map_status(
            fulfillment_status=raw.get("fulfillment_status"),
            payment_status=raw.get("payment_status"),
            is_draft=is_draft,
        )
        pay_status = _map_pay_status(raw.get("payment_status"))
        pay_type: OrderPayType = (
            "cod" if metadata.get("payment_method") == "cash_on_delivery"
            else "confirmed"
        )

        # ---- items + pieces ----
        items_raw = raw.get("items") or []
        items_count = len(items_raw)
        pieces = sum(int(i.get("quantity", 0)) for i in items_raw)

        # ---- due date estimate (created_at + 24h, marker en
        # data_completeness_missing) ----
        due_iso, due_time = _estimate_due(created_at_ms)

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
            overdue=False,  # derivado client-side
            priority=priority,
            agent=agent,
            created_at_ms=created_at_ms,
            updated_at_ms=updated_at_ms,
        )

    def _build_detail(
        self, raw: dict[str, Any], *, is_draft: bool
    ) -> OrderDetailDTO:
        summary = self._build_summary(raw, is_draft=is_draft)

        # Items con detalle
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

        # Timeline minimo (created + cancelled/captured cuando aplique)
        timeline: list[OrderTimelineEventDTO] = []
        if summary.created_at_ms:
            timeline.append(
                OrderTimelineEventDTO(
                    type="created",
                    label="Pedido creado" + (" (draft)" if is_draft else ""),
                    timestamp_ms=summary.created_at_ms,
                    detail=None,
                )
            )
        # Si el order tiene `transactions[]` con payments capturados, mostramos
        # el primero. Medusa los entrega ordenados por created_at.
        for tx in (raw.get("transactions") or []):
            ts = _iso_to_ms(tx.get("created_at"))
            if not ts:
                continue
            timeline.append(
                OrderTimelineEventDTO(
                    type="payment_captured",
                    label="Pago capturado",
                    timestamp_ms=ts,
                    detail=f"{_to_int_cop(tx.get('amount', 0)):,} {raw.get('currency_code', 'COP').upper()}".replace(",", "."),
                )
            )
            break  # primer transaction = el primer cargo

        # Payment method label
        pay_method_raw = (raw.get("metadata") or {}).get("payment_method")
        payment_method_label = {
            "transfer": "Transferencia",
            "card": "Tarjeta",
            "cash_on_delivery": "Contra entrega",
        }.get(pay_method_raw)

        # Slots que NO tenemos → marker
        missing = list(_MISSING_SLOTS_BASE)
        # Si no hay transactions, faltan datos de pago tambien
        if not raw.get("transactions"):
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
            notes=[],
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


def _estimate_due(created_at_ms: int) -> tuple[str | None, str | None]:
    """Estimate de fecha de entrega: created_at + 24h, formateado YYYY-MM-DD.
    Marca slot 'due_date' en data_completeness_missing para que la UI
    indique que es estimado."""
    if not created_at_ms:
        return (None, None)
    dt = datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc)
    due = dt + timedelta(days=1)
    return (due.date().isoformat(), "—")


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


__all__ = [
    "MedusaOrderQuery",
    # Helpers exportados solo para tests
    "_map_status",
    "_map_pay_status",
    "_initials",
    "_color_from_id",
    "_to_int_cop",
    "_iso_to_ms",
]


# Silenciar advertencia de unused import (sin esto ruff podria comer cast)
_ = cast
