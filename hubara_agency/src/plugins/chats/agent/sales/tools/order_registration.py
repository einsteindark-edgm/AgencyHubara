"""Tool: RegisterOrderTool.

Registra un pedido formalmente cuando la venta cierra con éxito
(`COMPRA_EXITOSA`). El backend de verdad es **Medusa v2 Draft Orders**
(POST /admin/draft-orders) — mismo Medusa que sirve el catálogo. La tool
delega al `OrderRegistrationPort` (DEHA-style hexagonal): el adapter live
es `MedusaOrderRegistration`, con fallback a `StubOrderRegistration` si
las env vars (`MEDUSA_REGION_ID`, `MEDUSA_SALES_CHANNEL_ID`) no están
seteadas.

DEHA:
  * Tool INERTE — no importa `httpx`, no importa `temporal_client`. Solo
    depende de `OrderRegistrationPort` (abstraccion en
    `src/platform/orders/port.py`).
  * R-JSON: input/output JSON-serializable (envelope textual al LLM).
  * R-DIP: el port se inyecta por constructor desde el composition root
    (`src/plugins/chats/workers/sales.py`), no se construye aqui.

Patrón LLM (ver TOOLS.md §"Reglas de cierre de venta"):
  1. Cliente completa el Flow (`nfm_reply`) o manda los datos por texto.
  2. LLM llama `verify_order_for_checkout(items)` → verified=True.
  3. LLM llama `present_order_confirmation(items, shipping, payment)`.
  4. Cliente apreta "✅ Confirmar".
  5. LLM llama `register_order(...)` con los datos finales.   ← ESTA TOOL
  6. Si `registered=true` → LLM llama
     `manage_conversation_tag(tag="COMPRA_EXITOSA", motivo=...)`.
  6. Si `registered=false` (Medusa caído / config rota) → LLM llama
     `escalate_to_human(reason_category="ORDER_REGISTRATION_FAILED")`.
  7. LLM despide al cliente con un mensaje cálido.

Esta secuencia es la única forma legítima de cerrar una venta. El LLM NO
debe poner `COMPRA_EXITOSA` sin haber llamado `register_order` antes (y
sin haber recibido `registered=true`).

Resiliencia (la pregunta del usuario sobre Temporal):
  * `HttpMedusaClient._request` ya tiene tenacity-with-backoff sobre
    `httpx.TransportError` (transient network).
  * Si Medusa devuelve 5xx persistente o esta caido, el adapter convierte
    a `OrderRegistrationResult(success=False)` — la activity de Temporal
    (`execute_tool`) NO falla (no levantamos excepciones), asi el LLM
    recibe el envelope con `registered=false` + instruccion explicita de
    escalar. El humano cierra desde el dashboard con los datos guardados.
  * Si quisieramos hard-retry (Temporal-native), bastaria con eliminar el
    `try/except` del adapter y dejar burbujear `MedusaAPIError` — Temporal
    reintentaria la activity completa segun su `RetryPolicy`. Por ahora
    preferimos el envelope explicito para que el LLM SIEMPRE pueda
    comunicarse con el cliente (vs un hard-failure que dejaria al cliente
    colgado mientras los retries pasan).
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from exoclaw.agent.tools import ToolBase, ToolContext
from loguru import logger

from src.platform.config import WORKSPACE_VAULT_DIR
from src.sdk.connectorkit import (
    DiscountedUnits,
    LineDiscount,
    QuotaLockTimeout,
    QuotaStoreError,
    enqueue_capi_event,
    normalize_capi_contents,
    product_retailer_id,
)
from src.platform.orders.port import (
    OrderItem,
    OrderRegistrationPort,
    OrderRegistrationResult,
    OrderShipping,
)
from src.platform.orders.reconciliation import STATUS_PENDING
from src.plugins.chats.agent.sales.config.shipping import (
    CASH_ON_DELIVERY_MIN_PRODUCTS_COP,
    SHIPPING_COP_PARAM_DESCRIPTION,
    SHIPPING_RATE_RULE,
    is_published_shipping_rate,
)
from src.plugins.chats.agent.sales.pricing import (
    accepted_prices,
    catalog_unit_price,
    format_cop,
)
from src.platform.orders.stub import StubOrderRegistration
from src.plugins.chats.agent.sales.use_cases.coupon_quota import (
    REASON_QUOTA_UNAVAILABLE,
    confirmed_split,
    line_discounts_from_key,
    resolve_item_variants,
    split_key,
)
from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import get_active_episode
from src.plugins.chats.agent.sales.use_cases.coupons import (
    applied_coupon,
    coupon_discount_for_items,
    promotion_from_snapshot,
    quota_product_ids,
)
from src.plugins.chats.agent.sales.use_cases.episode_lifecycle import (
    attach_order_to_active_episode,
)
from src.sdk.catalogkit import (
    CatalogPort,
    ProductNotFoundError,
    product_includes_portavelas,
)


def _order_reference(raw_payload: dict[str, Any] | None) -> str | None:
    """Referencia humana del pedido para mensajes al cliente: "#22 (Plegaria
    de Luz)" — mismo estilo que la notificación ETA. El cliente no sabe qué
    es `order_01KXV...`.

    Medusa asigna `display_id` AL CREAR el draft (POST /admin/draft-orders),
    así que existe desde el registro — no depende de que el humano agende la
    entrega. `None` si el provider no lo trae (stub) → el renderer cae al
    order_id crudo.
    """
    raw = raw_payload or {}
    display_id = raw.get("display_id")
    if display_id is None:
        return None
    # Un producto puede venir en varias líneas (unidades con cupón y a precio
    # de lista, L-26): el cliente lee UN producto con su cantidad total.
    quantities: dict[str, int] = {}
    for it in raw.get("items") or []:
        title = (it.get("title") or "").strip()
        if title:
            quantities[title] = quantities.get(title, 0) + int(it.get("quantity") or 0)
    parts = [
        f"{qty}× {title}" if qty > 1 else title
        for title, qty in list(quantities.items())[:3]
    ]
    if len(quantities) > 3:
        parts.append(f"y {len(quantities) - 3} más")
    label = ", ".join(parts)
    reference = f"#{display_id}"
    if label:
        reference = f"{reference} ({label})"
    return reference[:120]


def _with_discounted_units(
    items: list[OrderItem], line_discounts: tuple[LineDiscount, ...]
) -> list[OrderItem]:
    """Ítems del port con el reparto del cupón (`LineDiscount.index` es la
    posición del ítem en el pedido)."""
    groups: dict[int, list[DiscountedUnits]] = {}
    for line in line_discounts:
        groups.setdefault(line.index, []).append(
            DiscountedUnits(
                units=line.units,
                discount_unit_cop=line.discount_unit_cop,
                quota_id=line.quota_id,
            )
        )
    return [
        replace(item, discounted_units=tuple(groups.get(i, ())))
        for i, item in enumerate(items)
    ]


def _said(item: dict[str, Any], field: str) -> str | None:
    """Lo que el LLM mandó en `color`/`aroma` del ítem, o None."""
    value = str(item.get(field) or "").strip()
    return value or None


def _item_identity(it: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(it.get("handle") or ""),
        int(it.get("quantity") or 0),
        int(it.get("unit_price_cop") or 0),
        str(it.get("variant_label") or ""),
        str(it.get("color") or "").casefold(),
        str(it.get("aroma") or "").casefold(),
    )


def _items_key(items: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return sorted(_item_identity(it) for it in items)


def _registered_split_for_same_order(
    metadata: dict[str, Any], items: list[dict[str, Any]], payment_method: str, code: str
) -> tuple[LineDiscount, ...] | None:
    """El reparto con cupo del pedido YA registrado en el episodio activo si
    `items` es ese mismo pedido (reintento), puesto sobre `items` aunque
    vengan en otro orden; None si es otro pedido."""
    record = metadata.get("registered_order")
    episode = get_active_episode(metadata) or {}
    registered_items = record.get("items") if isinstance(record, dict) else None
    if (
        not isinstance(record, dict)
        or record.get("success") is not True
        or str(episode.get("order_id") or "") != str(record.get("order_id") or "")
        or record.get("coupon_code") != code
        or record.get("payment_method") != payment_method
        or not isinstance(registered_items, list)
        or _items_key(registered_items) != _items_key(items)
    ):
        return None
    # Cada línea del registro va a SU ítem en este pedido (mismo producto,
    # cantidad, precio y variante), no a la que quedó en su posición.
    free = list(range(len(items)))
    moved: dict[int, int] = {}
    for i, it in enumerate(registered_items):
        j = next(k for k in free if _item_identity(items[k]) == _item_identity(it))
        free.remove(j)
        moved[i] = j
    return tuple(
        sorted(
            (
                LineDiscount(
                    moved[int(line["index"])],
                    int(line["units"]),
                    int(line["discount_unit_cop"]),
                    quota_id=line.get("quota_id"),
                )
                for line in record.get("coupon_line_discounts") or []
            ),
            key=lambda d: d.index,
        )
    )


#: Un registro con cupo espera a lo sumo esto a que termine otro del mismo
#: código (el adapter de Medusa acota el suyo a ~45 s).
_QUOTA_LOCK_TIMEOUT_S = 60.0


class RegisterOrderTool(ToolBase):
    """Registra un pedido al cierre exitoso de la venta vía OrderRegistrationPort.

    Default `port = StubOrderRegistration()` para que la tool sea
    constructible sin DI explicito en tests legacy y dev local sin Medusa.
    En produccion, el composition root inyecta `MedusaOrderRegistration`
    via `get_order_registration_port()`.
    """

    name = "register_order"
    description = (
        "Registra formalmente el pedido del cliente en Medusa cuando la "
        "venta cerró exitosamente. Llámala SOLO después de que el cliente "
        "confirmó el pedido (vía `present_order_confirmation`) Y ya tienes "
        "todos los datos de envío (ciudad, barrio, dirección, teléfono, "
        "nombre de quien recibe, método de pago; cédula opcional). Esta "
        "tool crea un Draft Order en Medusa para que "
        "el equipo de Hubara lo procese. Lee el campo `registered` de la "
        "respuesta y sigue el `summary` del envelope: si es `true`, tag "
        "`CONFIRMADO_PAGO_PENDIENTE` + `escalate_to_human"
        "(reason_category='PAYMENT_VERIFICATION_PENDING')` con la despedida "
        "en su `customer_message` (NUNCA `COMPRA_EXITOSA`). Si es `false` "
        "con `error` (`shipping_mismatch`, `amount_mismatch`, "
        "`price_mismatch`, `missing_receiver_name`, `invalid_variant_attribute`, "
        "`quota_changed`, `quota_busy`), el pedido no se registró por un dato "
        "de la llamada o del cupo: haz lo que dice el `summary` y vuelve a "
        "intentarlo, sin escalar. Si es `false` sin "
        "`error` (Medusa caído / config rota), llama `escalate_to_human"
        "(reason_category='ORDER_REGISTRATION_FAILED')` para que un colega "
        "registre manualmente — los datos quedan persistidos en metadata "
        "para que el equipo los reconstruya. Si el cliente confirmó pero "
        "NO completó los datos de envío, NO uses esta tool — usa "
        "`escalate_to_human(reason_category='ORDER_PENDING_SHIPPING_DETAILS')`."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": 1,
                "description": (
                    "Items del pedido. Cada item: handle (snapshot), "
                    "quantity, unit_price_cop, opcionalmente "
                    "variant_label (ej. 'Lavanda', 'Azul')."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "handle": {"type": "string", "minLength": 1},
                        "quantity": {"type": "integer", "minimum": 1},
                        "unit_price_cop": {"type": "integer", "minimum": 0},
                        "variant_label": {
                            "type": "string",
                            "maxLength": 80,
                        },
                        "color": {
                            "type": "string",
                            "maxLength": 60,
                            "description": "Color elegido (de la lista del producto), si tiene.",
                        },
                        "aroma": {
                            "type": "string",
                            "maxLength": 60,
                            "description": "Aroma elegido (de la lista del producto), si tiene.",
                        },
                    },
                    "required": ["handle", "quantity", "unit_price_cop"],
                },
            },
            "shipping": {
                "type": "object",
                "description": "Datos de envío completos.",
                "properties": {
                    "city": {"type": "string", "minLength": 1},
                    "neighborhood": {"type": "string", "minLength": 1},
                    "address": {"type": "string", "minLength": 1},
                    "phone": {"type": "string", "minLength": 7},
                    "receiver_name": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "Nombre completo de quien recibe el pedido "
                            "(obligatorio — lo exige la transportadora)."
                        ),
                    },
                    "national_id": {
                        "type": "string",
                        "description": (
                            "Cédula de quien recibe (OPCIONAL — solo si el "
                            "cliente la dio; no insistas)."
                        ),
                    },
                },
                "required": [
                    "city",
                    "neighborhood",
                    "address",
                    "phone",
                    "receiver_name",
                ],
            },
            "payment_method": {
                "type": "string",
                "enum": ["transfer", "payment_link", "cash_on_delivery"],
                "description": (
                    "Método de pago elegido por el cliente: 'transfer' = "
                    "pago anticipado (Nequi/llave), 'payment_link' = link "
                    "de pago (con recargo), 'cash_on_delivery' = contra "
                    f"entrega (pedidos desde {format_cop(CASH_ON_DELIVERY_MIN_PRODUCTS_COP)} "
                    "COP en productos, inclusive)."
                ),
            },
            "subtotal_cop": {"type": "integer", "minimum": 0},
            "shipping_cop": {
                "type": "integer",
                "minimum": 0,
                "description": SHIPPING_COP_PARAM_DESCRIPTION,
            },
            "total_cop": {"type": "integer", "minimum": 1},
            "currency": {
                "type": "string",
                "default": "COP",
                "description": "Por ahora siempre 'COP'.",
            },
        },
        "required": [
            "items",
            "shipping",
            "payment_method",
            "subtotal_cop",
            "shipping_cop",
            "total_cop",
        ],
    }

    def __init__(
        self,
        workspace: str | Path,
        vault_dir: str | Path | None = None,
        port: OrderRegistrationPort | None = None,
        catalog: CatalogPort | None = None,
        quotas: Any = None,
        sales: Any = None,
        quota_lock: Any = None,
    ) -> None:
        """`quotas`/`sales`/`quota_lock`: cupo por unidad (central de cupones).
        Con cupo, el reparto se relee BAJO el candado del código antes de
        crear el draft (nunca se vende dos veces la última unidad)."""
        # Mismo patrón que `ManageConversationTagTool`: el `workspace` que
        # llega es el RUNTIME WORKSPACE CANONICO compartido — NO se usa
        # para metadata. `vault_dir` (DI-friendly): default vault canónico.
        self._workspace = Path(workspace)
        self._vault_dir = (
            Path(vault_dir) if vault_dir is not None else WORKSPACE_VAULT_DIR
        )
        # DI: el composition root pasa el port real
        # (`MedusaOrderRegistration`). Si nadie inyecta, usamos el stub —
        # esto permite que la tool sea constructible en tests sin tener que
        # mockear Medusa, y permite dev sin Medusa configurado.
        self._port: OrderRegistrationPort = port or StubOrderRegistration()
        # Incidente 943e6bff: la política del color del portavelas SOLO aplica
        # a los productos que lo traen (Dúo Zodiacal). Se decide acá, contra
        # el catálogo, y viaja determinista al envelope + a la decisión del
        # workflow. Sin catálogo (tests legacy / dev) la tool es conservadora:
        # NO lo menciona.
        self._catalog = catalog
        self._quotas = quotas
        self._sales = sales
        self._quota_lock = quota_lock

    def _quota_code(self, session_key: str) -> str | None:
        """Código del cupón aplicado si tiene cupo por unidad (hay que
        registrar bajo su candado); None si no."""
        if self._quotas is None:
            return None
        raw = applied_coupon(self._read_metadata(session_key))
        if raw is None:
            return None
        try:
            promotion = promotion_from_snapshot(raw["promotion"])
        except (TypeError, KeyError):
            return None
        try:
            has_quota = bool(self._quotas.get(promotion.id).quotas)
        except QuotaStoreError:
            has_quota = True  # ilegible: se registra bajo candado y falla cerrada
        return promotion.code if has_quota else None

    async def _portavelas_handles(self, items: list[dict[str, Any]]) -> list[str]:
        """Handles del pedido cuyo producto trae portavela según el catálogo.

        Conservador: handle desconocido, snapshot ausente o cualquier error
        del catálogo → ese ítem NO cuenta (antes que hablarle del portavelas
        a un comprador que no lo pidió)."""
        if self._catalog is None:
            return []
        found: list[str] = []
        for it in items:
            handle = str(it.get("handle") or "").strip()
            if not handle or handle in found:
                continue
            try:
                product = await self._catalog.get_by_handle(handle)
            except ProductNotFoundError:
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "🧾 [TOOL register_order] catálogo no disponible para "
                    "decidir portavelas handle={} err={}",
                    handle,
                    exc,
                )
                continue
            if product_includes_portavelas(product):
                found.append(handle)
        return found

    async def _capi_contents(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """``contents`` de CAPI por línea del pedido: ``retailer_id`` VIGENTE
        en Meta (SKU, o id de Medusa mientras no haya SKU), cantidad y precio
        unitario. Sin catálogo o handle desconocido → la línea se omite."""
        if self._catalog is None:
            return []
        out: list[dict[str, Any]] = []
        for it in items:
            handle = str(it.get("handle") or "").strip()
            if not handle:
                continue
            try:
                product = await self._catalog.get_by_handle(handle)
            except ProductNotFoundError:
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "🧾 [TOOL register_order] catálogo no disponible para "
                    "resolver retailer_id handle={} err={}",
                    handle,
                    exc,
                )
                continue
            out.append(
                {
                    "retailer_id": product_retailer_id(product),
                    "quantity": it.get("quantity"),
                    "unit_price_cop": it.get("unit_price_cop"),
                }
            )
        return normalize_capi_contents(out)

    def _read_metadata(self, session_key: str) -> dict[str, Any]:
        """metadata.json de la sesión ({} si no existe / corrupto)."""
        metadata_file = self._vault_dir / session_key / "metadata.json"
        if not metadata_file.exists():
            return {}
        try:
            data = json.loads(metadata_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _session_attribution(self, session_key: str) -> dict[str, Any] | None:
        """Atribución CTWA de la sesión (`origin` de metadata.json): el
        `source_id` del referral es el AD ID de Meta. Best-effort: sesión
        directa / metadata ausente o rota → None (la orden se registra igual)."""
        metadata_file = self._vault_dir / session_key / "metadata.json"
        try:
            origin = json.loads(metadata_file.read_text(encoding="utf-8")).get("origin") or {}
        except (OSError, json.JSONDecodeError):
            return None
        source_id = origin.get("source_id")
        if not source_id:
            return None
        return {
            "meta_ad_id": str(source_id),
            "attribution_channel": origin.get("channel"),
        }

    async def _price_mismatches(
        self, session_key: str, items: list[OrderItem]
    ) -> list[tuple[str, int, int]]:
        """`(handle, precio_pasado, precio_esperado)` por ítem cuyo precio no
        es del catálogo ni el live verificado. Catálogo caído / handle no
        resoluble → sin referencia → no se reporta (degradado)."""
        if self._catalog is None:
            return []
        mismatches: list[tuple[str, int, int]] = []
        for it in items:
            try:
                product = await self._catalog.get_by_handle(it.handle)
            except Exception:  # noqa: BLE001 — sin referencia para este ítem
                continue
            snapshot = catalog_unit_price(product)
            accepted = accepted_prices(
                self._vault_dir, session_key, it.handle, snapshot_price_cop=snapshot
            )
            if accepted and int(it.unit_price_cop) not in accepted:
                expected = snapshot if snapshot in accepted else max(accepted)
                mismatches.append((it.handle, int(it.unit_price_cop), int(expected)))
        return mismatches

    async def execute_with_context(
        self,
        ctx: ToolContext,
        items: list[dict[str, Any]],
        shipping: dict[str, Any],
        payment_method: str,
        subtotal_cop: int,
        shipping_cop: int,
        total_cop: int,
        currency: str = "COP",
    ) -> str:
        args = dict(
            items=items, shipping=shipping, payment_method=payment_method,
            subtotal_cop=subtotal_cop, shipping_cop=shipping_cop,
            total_cop=total_cop, currency=currency,
        )
        code = self._quota_code(ctx.session_key)
        if code is None:
            return await self._execute(ctx, **args)
        if self._quota_lock is None:
            # Con cupo y sin candado se podría vender dos veces la última
            # unidad: falla cerrada.
            logger.error(
                "🧾 [TOOL register_order] cupo sin candado configurado session={} code={}: falla cerrada",
                ctx.session_key, code,
            )
            return json.dumps(
                {
                    "registered": False,
                    "order_id": None,
                    "error_detail": "quota_unavailable",
                    "summary": (
                        f"No pude confirmar las unidades con descuento del cupón {code}: el "
                        "pedido NO se registró. Escala a un colega "
                        "(`escalate_to_human`, ORDER_REGISTRATION_FAILED)."
                    ),
                },
                ensure_ascii=False,
            )
        # Cupo por unidad: "releer lo vendido → repartir → crear el draft"
        # corre bajo el candado del código (dos clientes y la última unidad).
        try:
            async with self._quota_lock.hold(code, timeout_s=_QUOTA_LOCK_TIMEOUT_S):
                return await self._execute(ctx, **args)
        except QuotaLockTimeout:
            logger.warning(
                "🧾 [TOOL register_order] quota_busy session={} code={} (otro registro tiene el candado)",
                ctx.session_key, code,
            )
            return json.dumps(
                {
                    "registered": False,
                    "order_id": None,
                    "error_detail": "quota_busy",
                    "error": "quota_busy",
                    "summary": (
                        f"Otro pedido con el cupón {code} se está registrando en este "
                        "momento. Espera unos segundos y vuelve a llamar `register_order` "
                        "con los mismos datos."
                    ),
                },
                ensure_ascii=False,
            )

    async def _execute(
        self,
        ctx: ToolContext,
        items: list[dict[str, Any]],
        shipping: dict[str, Any],
        payment_method: str,
        subtotal_cop: int,
        shipping_cop: int,
        total_cop: int,
        currency: str = "COP",
    ) -> str:
        # Convertir JSON-schema params a frozen DTOs del port.
        order_items = [
            OrderItem(
                handle=str(it["handle"]),
                quantity=int(it["quantity"]),
                unit_price_cop=int(it["unit_price_cop"]),
                variant_label=str(it["variant_label"])
                if it.get("variant_label") is not None else None,
            )
            for it in items
        ]
        # Requisito 2026-08-31: la transportadora exige el nombre de quien
        # recibe — sin él NO se registra (el LLM debe recolectarlo primero).
        # La cédula es opcional y viaja solo si el cliente la dio.
        receiver_name = str(shipping.get("receiver_name") or "").strip()
        if not receiver_name:
            return json.dumps(
                {
                    "registered": False,
                    "order_id": None,
                    "error_detail": "missing_receiver_name",
                    "error": "missing_receiver_name",
                    "summary": (
                        "Falta el NOMBRE DE QUIEN RECIBE el pedido (la "
                        "transportadora lo exige). Pregúntale al cliente "
                        "quién recibe el paquete, guárdalo con "
                        "`set_order_slot(nombre_recibe=...)` y vuelve a "
                        "llamar `register_order` incluyendo "
                        "`shipping.receiver_name`. La cédula es opcional."
                    ),
                },
                ensure_ascii=False,
            )
        national_id_raw = str(shipping.get("national_id") or "").strip()
        order_shipping = OrderShipping(
            city=str(shipping["city"]),
            neighborhood=str(shipping["neighborhood"]),
            address=str(shipping["address"]),
            phone=str(shipping["phone"]),
            receiver_name=receiver_name,
            national_id=national_id_raw or None,
        )

        # Envío = tarifa mínima publicada para la ciudad (decisión del operador
        # 2026-09-23: el envío lo cobra la transportadora, sin descuentos ni
        # envío gratis). SEC-07 solo caza un total desconectado; un envío $0
        # con el total "cuadrado" llegaba a Medusa y a las instrucciones de
        # pago como "sin costo" (L-19). Va ANTES de SEC-07: con envío 0 y el
        # total sumado con la tarifa, SEC-07 diría "total esperado = subtotal"
        # y el modelo gastaría un reintento quitando el envío del total.
        if not is_published_shipping_rate(int(shipping_cop), order_shipping.city):
            logger.warning(
                "🧾 [TOOL register_order] SHIPPING_MISMATCH session={} city={} shipping_cop={}",
                ctx.session_key,
                order_shipping.city,
                shipping_cop,
            )
            return json.dumps(
                {
                    "registered": False,
                    "order_id": None,
                    "error_detail": "shipping_mismatch",
                    "error": "shipping_mismatch",
                    "summary": (
                        f"El envío que pasaste ({format_cop(int(shipping_cop))}) no es "
                        f"la tarifa publicada para {order_shipping.city}. El pedido NO "
                        f"se registró. {SHIPPING_RATE_RULE} Recalcula el total "
                        "(subtotal + envío − cupón) y llama de nuevo `register_order`; "
                        "si le dijiste al cliente otro valor de envío, acláraselo con "
                        "honestidad antes."
                    ),
                },
                ensure_ascii=False,
            )

        # Cupón aplicado en el episodio (`apply_coupon`): el descuento se
        # recomputa acá desde el snapshot + catálogo. NUNCA lo manda el LLM.
        metadata_before = self._read_metadata(ctx.session_key)
        # Color/aroma de cada ítem: un valor que el producto no tiene NO
        # registra nada (mismo contrato que present_order_confirmation).
        variants, invalid_variants = await resolve_item_variants(
            self._catalog, items, metadata_before,
            strict_products=quota_product_ids(metadata_before, self._quotas),
        )
        if invalid_variants:
            return json.dumps(
                {
                    "registered": False,
                    "order_id": None,
                    "error_detail": "invalid_variant_attribute",
                    "error": "invalid_variant_attribute",
                    "summary": (
                        "; ".join(v.message() for v in invalid_variants)
                        + ". El pedido NO se registró: confirma con el cliente una opción "
                        "de la lista y vuelve a presentar la confirmación."
                    ),
                },
                ensure_ascii=False,
            )
        # Color/aroma que se despachan (premortem C1): los productos "Unico"
        # no tienen variante que los diga, así que viajan en la línea. El
        # canónico de la lista del producto si lo hay; si no, lo que dijo el
        # cliente tal cual.
        order_items = [
            replace(order_item, color=v.color or _said(it, "color"), aroma=v.aroma or _said(it, "aroma"))
            for order_item, v, it in zip(order_items, variants, items)
        ]
        discount = await coupon_discount_for_items(
            metadata_before, self._catalog, items, shipping_cop=shipping_cop,
            quotas=self._quotas, sales=self._sales, variants=variants,
        )
        if discount is not None and discount.quota:
            # Reintento del MISMO pedido ya registrado en este episodio: su
            # propio draft ya cuenta como vendido, así que el reparto fresco
            # saldría distinto. Se reusa el reparto registrado → mismo
            # fingerprint → el adapter devuelve el draft existente.
            previous = _registered_split_for_same_order(
                metadata_before, items, payment_method, discount.code
            )
            if previous is not None:
                discount = replace(
                    discount,
                    line_discounts=previous,
                    discount_cop=sum(d.units * d.discount_unit_cop for d in previous),
                    reason=None,
                )
        # Cupo por unidad: el reparto recién releído (bajo el candado) tiene
        # que ser el que vio el cliente en la confirmación. Si cambió (otro
        # cliente se llevó unidades), NO se crea el draft: total nuevo y a
        # confirmar otra vez. El reparto se compara sin importar el orden de
        # los ítems (el LLM puede listarlos en otro orden al registrar).
        quota_unreadable = False
        if discount is not None and discount.quota:
            seen = confirmed_split(metadata_before, discount.code)
            fresh = split_key(discount.line_discounts, items, variants)
            confirmed_lines = (
                line_discounts_from_key(seen, items, variants)
                if seen and discount.reason == REASON_QUOTA_UNAVAILABLE
                else None
            )
            if confirmed_lines is not None:
                # Lo vendido no se pudo releer (Medusa o el vault): NO es
                # "cambiaron las unidades" — el cliente leería un total falso.
                # No se crea nada: el pedido queda guardado con el reparto que
                # el cliente CONFIRMÓ, la reconciliación lo reintenta bajo el
                # candado (relee el cupo) y el bot escala como con Medusa caído.
                discount = replace(
                    discount,
                    line_discounts=confirmed_lines,
                    discount_cop=sum(d.units * d.discount_unit_cop for d in confirmed_lines),
                    reason=None,
                )
                quota_unreadable = True
            elif seen is None or seen != fresh:
                new_total = (
                    sum(int(it["unit_price_cop"]) * int(it["quantity"]) for it in items)
                    + shipping_cop
                    - discount.discount_cop
                )
                logger.info(
                    "🧾 [TOOL register_order] quota_changed session={} code={} reason={} confirmed={} fresh={}",
                    ctx.session_key, discount.code, discount.reason, seen, fresh,
                )
                if seen is None:
                    # El cliente nunca vio qué unidades llevan descuento.
                    why = (
                        f"El cliente todavía no vio qué unidades llevan el descuento de "
                        f"{discount.code}: el pedido NO se registró."
                    )
                elif discount.reason == REASON_QUOTA_UNAVAILABLE:
                    why = (
                        "El pedido no es el que el cliente confirmó y no pude releer las "
                        f"unidades con descuento de {discount.code}: el pedido NO se registró."
                    )
                else:
                    why = (
                        f"Mientras el cliente confirmaba cambiaron las unidades con "
                        f"descuento de {discount.code}: el total ahora es "
                        f"{format_cop(new_total)}. El pedido NO se registró. Explícale "
                        "el cambio con honestidad."
                    )
                return json.dumps(
                    {
                        "registered": False,
                        "order_id": None,
                        "error_detail": "quota_changed",
                        "error": "quota_changed",
                        "new_total_cop": new_total,
                        "new_discount_cop": discount.discount_cop,
                        "summary": (
                            f"{why} Vuelve a llamar `present_order_confirmation` para que "
                            "el cliente confirme el total."
                        ),
                    },
                    ensure_ascii=False,
                )
        if discount is not None and self._catalog is None:
            logger.warning(
                "🧾 [TOOL register_order] cupón {} sin catálogo: descuento solo si la promo es global",
                discount.code,
            )
        discount_cop = discount.discount_cop if discount else 0
        coupon_code = discount.code if discount and discount_cop > 0 else None
        # Pedido #44: el reparto por unidad viaja en cada ítem y el adapter lo
        # escribe como precio de línea — Medusa no aplica la promoción a un
        # draft. Solo con cupón: los ports/fakes sin estos kwargs siguen
        # andando.
        coupon_kwargs: dict[str, Any] = {}
        if discount is not None and coupon_code:
            order_items = _with_discounted_units(order_items, discount.line_discounts)
            coupon_kwargs = {"coupon_code": coupon_code, "discount_cop": discount_cop}

        # SEC-07: consistencia de montos server-side. El LLM manda los precios;
        # recomputamos el subtotal desde los line items para que un total
        # inventado (o manipulado por el cliente vía prompt injection) NO cree un
        # draft order. NO depende del catálogo — coupon-ready: cuando lleguen
        # cupones, `discount_cop` deja de ser 0. NOTA: esto caza el total
        # desconectado de los ítems, NO un unit_price bajado proporcionalmente
        # (eso requiere comparar contra catálogo); el gate humano de pago sigue
        # siendo el control primario de integridad de precio.
        computed_subtotal = sum(it.unit_price_cop * it.quantity for it in order_items)
        expected_total = computed_subtotal + shipping_cop - discount_cop
        if subtotal_cop != computed_subtotal or total_cop != expected_total:
            logger.warning(
                "🧾 [TOOL register_order] AMOUNT_MISMATCH session={} "
                "subtotal_recibido={} subtotal_computado={} total_recibido={} total_esperado={}",
                ctx.session_key,
                subtotal_cop,
                computed_subtotal,
                total_cop,
                expected_total,
            )
            return json.dumps(
                {
                    "registered": False,
                    "order_id": None,
                    "error_detail": "amount_mismatch",
                    "error": "amount_mismatch",
                    "summary": (
                        "Los montos NO cuadran con los ítems del pedido: "
                        f"subtotal esperado={computed_subtotal} (recibido {subtotal_cop}), "
                        f"total esperado={expected_total} (recibido {total_cop}). "
                        "Recalcula el pedido con los precios REALES del catálogo "
                        "(subtotal = suma de unit_price×cantidad; total = subtotal "
                        "+ envío"
                        + (
                            f" − descuento del cupón {coupon_code} ${discount_cop:,} COP"
                            if coupon_code
                            else ""
                        )
                        + ") y llama de nuevo `register_order` con los montos "
                        "correctos. No inventes precios ni totales."
                    ).replace(",", "."),
                },
                ensure_ascii=False,
            )

        # SEC-07b (run ebbc203d, 2026-09-16 + inyección de precios): el precio
        # UNITARIO debe ser el del catálogo (snapshot) o el live que verificó
        # `verify_order_for_checkout` en esta sesión. SEC-07 solo caza un total
        # desconectado de los ítems; un unit_price bajado ("cóbrame 30.000") o
        # sacado del anuncio (45.000 con el set a 49.500) cuadraba y llegaba a
        # Medusa. Sin referencia (catálogo caído) se degrada a SEC-07.
        price_mismatches = await self._price_mismatches(ctx.session_key, order_items)
        if price_mismatches:
            logger.warning(
                "🧾 [TOOL register_order] PRICE_MISMATCH session={} {}",
                ctx.session_key,
                price_mismatches,
            )
            detail = "; ".join(
                f"{h}: pasaste {format_cop(p)}, catálogo {format_cop(e)}"
                for h, p, e in price_mismatches
            )
            return json.dumps(
                {
                    "registered": False,
                    "order_id": None,
                    "error_detail": "price_mismatch",
                    "error": "price_mismatch",
                    "summary": (
                        f"Los precios NO son los del catálogo: {detail}. El "
                        "pedido NO se registró. Llama `verify_order_for_checkout` "
                        "y usa EXACTAMENTE `unit_price_cop` / `subtotal_cop` del "
                        "envelope; si le habías dicho otro precio al cliente, "
                        "acláraselo con honestidad antes de registrar. Nunca "
                        "inventes ni negocies precios (descuentos → "
                        "escalate_to_human('DISCOUNT_REQUEST'))."
                    ),
                },
                ensure_ascii=False,
            )

        # Delegar al port (Medusa adapter o stub). La atribución CTWA viaja a
        # la metadata de la orden Medusa — es el join venta↔campaña del
        # dashboard (2026-07-09; sin esto Medusa no sabe de qué ad vino la venta).
        result: OrderRegistrationResult
        if quota_unreadable:
            # Queda como un registro fallido `pending` (abajo) con el reparto
            # confirmado: la reconciliación lo reintenta bajo el candado.
            result = OrderRegistrationResult(
                success=False,
                order_id=None,
                provider="medusa",
                error_detail=(
                    f"quota_unavailable: no pude releer las unidades vendidas del cupón "
                    f"{coupon_code}; el pedido queda guardado para reintentarlo"
                ),
            )
        else:
            result = await self._port.register_order(
                session_key=ctx.session_key,
                items=order_items,
                shipping=order_shipping,
                payment_method=payment_method,
                subtotal_cop=subtotal_cop,
                shipping_cop=shipping_cop,
                total_cop=total_cop,
                currency=currency,
                attribution=self._session_attribution(ctx.session_key),
                **coupon_kwargs,
            )

        # Generar un fallback order_id si el port no devolvio uno (no
        # deberia pasar — el stub siempre devuelve uno, el adapter Medusa
        # solo deja None si fallo). Si fallo, igual queremos un id de
        # auditoria local para reconciliacion manual.
        local_audit_id = (
            result.order_id
            or f"AUDIT-{ctx.session_key}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        )

        logger.info(
            "🧾 [TOOL register_order] session={} success={} provider={} order_id={} total={} {}",
            ctx.session_key,
            result.success,
            result.provider,
            result.order_id or "(none)",
            total_cop,
            currency,
        )

        # ------------------------------------------------------------
        # Persist en metadata.json — siempre, aun si fallo (audit log).
        # ------------------------------------------------------------
        registered_record: dict[str, Any] = {
            "order_id": result.order_id or local_audit_id,
            "session_key": ctx.session_key,
            "provider": result.provider,
            "success": result.success,
            "error_detail": result.error_detail,
            "customer_id": result.customer_id,
            "items": items,
            # El reintento de reconciliación escribe el MISMO color/aroma.
            "item_variants": [{"color": it.color, "aroma": it.aroma} for it in order_items],
            "shipping": shipping,
            "payment_method": payment_method,
            "subtotal_cop": subtotal_cop,
            "shipping_cop": shipping_cop,
            "discount_cop": discount_cop,
            "coupon_code": coupon_code,
            # El reparto del cupón: el reintento de reconciliación escribe en
            # Medusa las MISMAS líneas con descuento (pedido #44).
            **(
                {
                    "coupon_line_discounts": [
                        {
                            "index": line.index,
                            "units": line.units,
                            "discount_unit_cop": line.discount_unit_cop,
                            **({"quota_id": line.quota_id} if line.quota_id else {}),
                        }
                        for line in discount.line_discounts
                    ],
                }
                if discount is not None and coupon_code
                else {}
            ),
            "total_cop": total_cop,
            "currency": currency,
            "registered_at_ms": int(time.time() * 1000),
            "raw_provider_payload": result.raw_payload,
            # Identidad Meta (SKU = retailer_id) de cada línea, ya en la
            # forma `contents` de CAPI: Purchase/OrderCreated la llevan para
            # la coincidencia de catálogo. Best-effort (catálogo caído →
            # []), nunca bloquea el registro.
            "capi_contents": await self._capi_contents(items),
        }

        metadata_file = self._vault_dir / ctx.session_key / "metadata.json"
        metadata_file.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if metadata_file.exists():
            try:
                data = json.loads(metadata_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}

        if result.success:
            data["registered_order"] = registered_record
            # Episode lifecycle: anotar order_id en el episodio activo (NO
            # cerrar — el cierre lo hace manage_conversation_tag(COMPRA_EXITOSA)
            # que el agente invoca justo después). Defensivo: si no hay
            # episodio activo, attach_order_to_active_episode crea uno.
            attach_order_to_active_episode(
                data,
                order_id=registered_record["order_id"],
                now_ms=registered_record["registered_at_ms"],
                # Congela el ingreso de la venta en el episodio (major units
                # COP) — lo agrega `list_ads_campaigns` como `revenue` por
                # campaña sin tener que consultar Medusa en read-time (R-DIP).
                order_total_cop=total_cop,
                currency=currency,
            )
            # Auditoría CAPI 2026-09-08: pedido creado en Medusa =
            # OrderCreated (con valor) para el embudo de Meta. Lo envía el
            # flusher después del turno.
            try:
                enqueue_capi_event(
                    data,
                    event_name="OrderCreated",
                    session_id=ctx.session_key,
                    order_id=registered_record["order_id"],
                    value=total_cop,
                    currency=currency,
                    contents=registered_record["capi_contents"],
                    source="register_order",
                    now_ms=registered_record["registered_at_ms"],
                )
            except ValueError:
                pass
            # Pago anticipado (transfer) → el SISTEMA manda la llave Nequi y
            # los datos bancarios, no el LLM (caso wa_573125671604: el LLM
            # alucinó cuenta y NIT). Link de pago → el SISTEMA avisa el link
            # y su recargo (el link real lo genera el humano tras la
            # escalación). Encolamos el intent acá — determinista, pasa
            # aunque el LLM no emita ninguna tool más. El flush renderiza la
            # plantilla fija según `method`; los params NO llevan datos
            # bancarios (nunca pasan por el LLM ni por metadata).
            if payment_method in ("transfer", "payment_link"):
                intents = data.setdefault("pending_ui_intents", [])
                # Referencia humana ("#22 (Plegaria de Luz)") — el display_id
                # ya existe acá: Medusa lo asigna al crear el draft. El
                # order_id interno sigue viajando (idempotency key + audit).
                # Desglose productos + envío = total (requisito 2026-09-07,
                # run 943e6bff): los tres montos ya pasaron el chequeo
                # SEC-07 de arriba (subtotal = Σ ítems, total = subtotal +
                # envío), así que el flush los muestra sin recalcular nada.
                params: dict[str, Any] = {
                    "order_id": registered_record["order_id"],
                    "subtotal_cop": subtotal_cop,
                    "shipping_cop": shipping_cop,
                    "total_cop": total_cop,
                    "currency": currency,
                    "method": payment_method,
                }
                if coupon_code:
                    params["discount_cop"] = discount_cop
                    params["coupon_code"] = coupon_code
                reference = _order_reference(result.raw_payload)
                if reference:
                    params["order_reference"] = reference
                intents.append({
                    "id": f"payinstr-{registered_record['order_id']}",
                    "kind": "payment_instructions",
                    "params": params,
                    "analytics": {
                        "component_id": "payment_instructions",
                        "component_kind": "text",
                    },
                    "queued_at_ms": registered_record["registered_at_ms"],
                })
        else:
            # Falla: NO sobrescribimos `registered_order` (preserva exitosos
            # previos) pero apendiamos a `failed_order_registrations[]` para
            # que el humano sepa que hubo intento + tenga el payload completo.
            # `status=pending` habilita el loop de reconciliación
            # (platform/orders/reconciliation.py): un reintento automático
            # (script/cron) o manual (dashboard) lo marcará resolved/abandoned.
            registered_record["status"] = STATUS_PENDING
            failed_log = data.setdefault("failed_order_registrations", [])
            failed_log.append(registered_record)
        history = data.setdefault("registered_orders_history", [])
        history.append({
            "order_id": registered_record["order_id"],
            "provider": result.provider,
            "success": result.success,
            "ts_ms": registered_record["registered_at_ms"],
        })

        metadata_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # ------------------------------------------------------------
        # Envelope para el LLM.
        # ------------------------------------------------------------
        if result.success:
            portavelas_handles = await self._portavelas_handles(items)
            portavelas_included = bool(portavelas_handles)
            # Motivo sintético: lo lee la RED DE SEGURIDAD del workflow
            # (`ensure_payment_pending_closure_activity`) si el LLM no completa
            # la secuencia de cierre, para que el `status_history` / la
            # escalación tengan un texto coherente. La nota del portavelas
            # SOLO viaja si el pedido lo incluye (incidente 943e6bff).
            order_motivo = (
                f"Cliente confirmó pedido {result.order_id} por "
                f"${total_cop} {currency}, método {payment_method}; "
                "falta verificación humana del pago."
            )
            if portavelas_included:
                order_motivo += (
                    " Pendiente: enviarle al cliente foto de los colores "
                    "disponibles del portavelas para que escoja."
                )
            envelope = {
                "registered": True,
                "order_id": result.order_id,
                "provider": result.provider,
                "discount_cop": discount_cop,
                "coupon_code": coupon_code,
                # Decisión determinista contra el catálogo: ¿algún ítem trae
                # portavela? El guion de cierre condiciona la nota al humano
                # y el aviso al comprador a `portavelas.included`.
                "portavelas": {
                    "included": portavelas_included,
                    "handles": portavelas_handles,
                },
                # Fix integridad orden↔tag (ADR-001): decisión que el workflow
                # levanta en `TurnResult.order_registered_decision`. Hace
                # VISIBLE el registro al workflow para que garantice el cierre
                # "pago pendiente" + escalación aunque el LLM no emita las
                # tools siguientes.
                "order_registered": {
                    "session_id": ctx.session_key,
                    "order_id": result.order_id,
                    "payment_method": payment_method,
                    "total_cop": total_cop,
                    "currency": currency,
                    "motivo": order_motivo,
                    "portavelas_included": portavelas_included,
                },
                "summary": (
                    f"Pedido registrado en Medusa con ID {result.order_id}. "
                    f"Total: ${total_cop:,} {currency}. ".replace(",", ".")
                    + (
                        "El SISTEMA ya le envía al cliente los datos del "
                        "pago anticipado (llave Nequi y banco) — NO escribas "
                        "números de cuenta ni banco ni NIT ni ningún dato "
                        "de pago en tu mensaje (cualquier dato que escribas "
                        "tú es inventado). "
                        if payment_method == "transfer"
                        else ""
                    )
                    + (
                        "El SISTEMA ya le avisó al cliente que el link de "
                        "pago le llega por este chat, con su recargo — NO "
                        "inventes links ni montos con recargo. "
                        if payment_method == "payment_link"
                        else ""
                    )
                    + "Llama ahora "
                    "`manage_conversation_tag(tag='CONFIRMADO_PAGO_PENDIENTE', "
                    "motivo=...)` y luego `escalate_to_human(reason_category="
                    "'PAYMENT_VERIFICATION_PENDING', summary=..., "
                    "customer_message=<tu despedida>)` para que un colega "
                    "verifique el pago. Esa tool TERMINA tu turno: lo único que "
                    "el cliente lee es `customer_message`. "
                    + (
                        "Este pedido INCLUYE un producto con portavelas: "
                        "incluye en el summary la nota 'enviarle al cliente "
                        "foto de los colores disponibles del portavelas para "
                        "que escoja' y lo que pidió en las notas del pedido "
                        "(signo del plato, color de la vela). La despedida "
                        "(va en `customer_message`) es un mensaje breve de "
                        "agradecimiento que además le avise que le enviarán "
                        "una foto con los colores disponibles del portavelas "
                        "para que escoja el suyo. "
                        if portavelas_included
                        else "Este pedido NO incluye portavelas: NO menciones "
                        "el portavelas ni sus colores, ni en el summary ni al "
                        "cliente. La despedida (va en `customer_message`) es "
                        "un mensaje breve de agradecimiento. "
                    )
                    + "NO menciones la verificación del pago "
                    "ni que alguien va a revisar nada (un colega se encarga por "
                    "detrás). NO uses "
                    "`COMPRA_EXITOSA` — esa tag la pone el equipo tras verificar "
                    "el pago."
                ),
            }
        else:
            envelope = {
                "registered": False,
                "order_id": None,
                "provider": result.provider,
                "error_detail": result.error_detail,
                "audit_id": local_audit_id,
                "summary": (
                    "El sistema de pedidos (Medusa) NO aceptó el registro: "
                    f"{result.error_detail or 'unknown error'}. Los datos "
                    f"quedaron guardados localmente con audit_id={local_audit_id} "
                    "para que un colega pueda completarlo manualmente. "
                    "Llama AHORA `escalate_to_human"
                    "(reason_category='ORDER_REGISTRATION_FAILED', "
                    "summary='cliente cerró pedido pero Medusa rechazó el "
                    "registro', customer_message='Tu pedido quedó tomado 🤍. "
                    "Un colega del equipo te confirma por este mismo chat.')` "
                    "— esa tool termina tu turno. NO uses "
                    "`manage_conversation_tag(COMPRA_EXITOSA)` — la venta NO "
                    "está formalmente cerrada hasta que el equipo la registre."
                ),
            }

        return json.dumps(envelope, ensure_ascii=False)
