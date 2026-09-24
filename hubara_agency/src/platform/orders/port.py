"""OrderRegistrationPort — abstraccion para registrar un pedido formal en la
fuente de la verdad al cierre exitoso de una venta.

La fuente de la verdad de Hubara es **Medusa v2** (mismo backend que sirve
el catalogo de productos via `CatalogPort`). El adapter default es
`MedusaOrderRegistration` (`medusa_order.py`), que llama
`POST /admin/draft-orders`. Existe un `StubOrderRegistration` (`stub.py`)
para tests / dev sin Medusa configurado.

R-DIP: el Protocol vive aqui; los consumers (la tool `RegisterOrderTool`)
reciben una instancia via constructor injection.

R-JSON: los DTOs son `@dataclass(frozen=True)` planos, JSON-safe — la tool
los serializa al envelope que el LLM lee. Ningun campo es `Decimal` /
`datetime` / `Path` (todo str o int).

Por que un Port y no llamar `HttpMedusaClient` directo desde la tool:
  * El test `tools-no-temporal` (importlinter) + buenas practicas hexagonal
    requieren que la tool dependa de una abstraccion, no de httpx.
  * Permite el StubOrderRegistration para dev sin Medusa (cuando faltan
    `MEDUSA_REGION_ID` / `MEDUSA_SALES_CHANNEL_ID`).
  * Facilita testear la tool con un FakePort (ver
    `tests/plugins/chats/sales/test_register_order_tool.py`).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class DiscountedUnits:
    """Unidades de un item que llevan el descuento del cupon.

    `units` unidades pagan `OrderItem.unit_price_cop - discount_unit_cop`
    (pesos enteros). El reparto lo calcula Hubara con las reglas del cupon
    (L-19); el adapter solo lo escribe.
    """
    units: int
    discount_unit_cop: int
    #: Cupo por unidad que consumen estas unidades (central de cupones): la
    #: línea lo lleva como `metadata.coupon_quota_id` y de ahí se derivan
    #: las vendidas del cupo. None = cupón sin cupo.
    quota_id: str | None = None


@dataclass(frozen=True)
class OrderItem:
    """Item del pedido — input al port.

    `handle` se resuelve a `variant_id` dentro del adapter (via
    `MedusaProductService.list(handle=...)`). El adapter elige la
    variante por `variant_label` si esta presente, sino la primera.

    `unit_price_cop` es SIEMPRE el precio de lista; las unidades con
    descuento de cupon van en `discounted_units`.
    """
    handle: str
    quantity: int
    unit_price_cop: int
    variant_label: str | None = None
    discounted_units: tuple[DiscountedUnits, ...] = ()


@dataclass(frozen=True)
class OrderShipping:
    """Datos de envio — input al port.

    No incluye country_code: lo agrega el adapter desde
    `MedusaSettings.default_country` (default "co"). No incluye email:
    el adapter lo sintetiza desde `session_key` (`wa+{session}@hubara.local`).

    `receiver_name` (requisito 2026-08-31): nombre de quien recibe el
    pedido — va a `shipping_address.first_name/last_name` de la draft
    order. `national_id` (cedula, opcional) va a metadata. Defaults
    laxos para retro-compat con records persistidos pre-requisito
    (reconciliacion); la obligatoriedad la impone `RegisterOrderTool`.
    """
    city: str
    neighborhood: str
    address: str
    phone: str
    receiver_name: str = ""
    national_id: str | None = None


@dataclass(frozen=True)
class OrderRegistrationResult:
    """Resultado de `register_order(...)`.

    `success=True`  → `order_id` y `provider="medusa"` (o "stub") presentes.
    `success=False` → `error_detail` describe la falla; `order_id` puede ser
                      None o un id local de auditoria.

    `raw_payload` es opcional y contiene el dict crudo que el adapter devolvio
    (util para persistir el shape completo a `metadata.registered_order`).
    """
    success: bool
    order_id: str | None
    provider: str  # "medusa" | "stub"
    raw_payload: dict[str, Any] | None = None
    error_detail: str | None = None
    # Datos echo back para que la tool persista en metadata sin tener que
    # reconstruir desde los inputs. Util para auditoria.
    customer_id: str | None = None
    items_resolved: list[dict[str, Any]] = field(default_factory=list)


@runtime_checkable
class OrderRegistrationPort(Protocol):
    """Contrato para registrar un pedido formal en la fuente de la verdad.

    Implementaciones:
      * `MedusaOrderRegistration` (live, prod default) — `medusa_order.py`.
      * `StubOrderRegistration` (dev/test) — `stub.py`.

    Idempotency: el contrato NO garantiza idempotency por sesion; si el
    LLM llama 2 veces, el port puede crear 2 draft orders distintas. La
    tool `RegisterOrderTool` mitiga esto persistiendo `registered_order`
    + `registered_orders_history` en metadata.json para auditoria.
    """

    async def register_order(
        self,
        *,
        session_key: str,
        items: list[OrderItem],
        shipping: OrderShipping,
        payment_method: str,
        subtotal_cop: int,
        shipping_cop: int,
        total_cop: int,
        currency: str = "COP",
        attribution: dict[str, Any] | None = None,
        coupon_code: str | None = None,
        discount_cop: int = 0,
        shipping_discount_cop: int = 0,
    ) -> OrderRegistrationResult: ...

    # Cupón (Fase 0, pedido #44): `total_cop` ya viene descontado (lo
    # recomputa la tool desde el snapshot del episodio, L-19) y el reparto
    # viaja en `OrderItem.discounted_units` (productos) o en
    # `shipping_discount_cop` (envío). `coupon_code` / `discount_cop` quedan
    # como auditoría. Los callers mandan estos kwargs SOLO cuando hay cupón,
    # así los adapters/fakes viejos siguen siendo compatibles.


def order_fingerprint(items: list[OrderItem], total_cop: int, payment_method: str) -> str:
    """Hash estable y corto del contenido de la orden.

    Dos `register_order` con el MISMO contenido (retry de Temporal / doble
    llamada del LLM / reconciliación) producen el mismo fingerprint; una
    compra distinta del mismo cliente produce uno distinto. El adapter de
    Medusa lo guarda en `metadata.order_fingerprint` del draft y lo usa en su
    pre-check de idempotencia; la reconciliación lo usa para no contar como
    vendido el draft del mismo pedido que reintenta (L-28).

    Determinístico: se ordenan los items para que el orden de llegada no
    cambie el hash. El reparto del cupón (`discounted_units`) es contenido
    del pedido, pero solo entra cuando existe: sin cupón el hash es el de
    siempre y un reintento que cruza un deploy sigue encontrando su draft.
    """
    parts = sorted(
        f"{it.handle}:{it.quantity}:{it.unit_price_cop}:{it.variant_label or ''}"
        + "".join(
            f":-{g.units}x{g.discount_unit_cop}" + (f"@{g.quota_id}" if g.quota_id else "")
            for g in it.discounted_units
        )
        for it in items
    )
    raw = "|".join(parts) + f"|total={total_cop}|pay={payment_method}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
