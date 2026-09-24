"""Aplicar un código de cupón al episodio activo — lo comparten la tool
`apply_coupon` (el cliente da el código) y el webhook cuando el cliente
responde a una campaña que anuncia un cupón (se aplica solo).

Conversación de prueba del 2026-09-24 (campaña `mkt-06d64ff582`, cupón
AMOR2026 con cupo por unidad): el cliente contestó "Me gusta", el bot nunca
llamó `apply_coupon` (la nota decía "si lo menciona") y ofreció todos los
aromas y colores a precio lleno.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable

from src.sdk.connectorkit import (
    PromotionDTO,
    PromotionsUnavailableError,
    normalize_coupon_code,
    resolve_coupon,
)

from src.plugins.chats.agent.sales.use_cases.coupon_quota import (
    as_eligible,
    quota_offer,
)
from src.plugins.chats.agent.sales.use_cases.coupons import (
    eligible_products,
    set_applied_coupon,
)

#: Motivos pasajeros (Medusa o el cupo no respondieron): volver a intentarlo
#: con `apply_coupon` tiene sentido. El resto es definitivo.
TRANSIENT_REASONS = frozenset({"unavailable", "quota_unavailable"})


#: Por qué un cupón no se aplica — para que el bot se lo diga al cliente.
COUPON_REASON_TEXT = {
    "invalid_format": "El código no tiene una forma válida (solo letras y números, sin guiones ni espacios).",
    "not_found": "Ese código no existe.",
    "inactive": "Ese cupón ya no está activo.",
    "not_started": "Ese cupón todavía no empieza a regir.",
    "expired": "Ese cupón ya venció.",
    "budget_exhausted": "Ese cupón ya se agotó.",
    "unavailable": "No pude validar el cupón ahora mismo (sistema de promociones caído).",
    "scope_unresolved": "No pude confirmar a qué productos aplica ese cupón, así que no lo apliqué.",
    "quota_unavailable": "No pude confirmar cuántas unidades con descuento quedan, así que no apliqué el cupón.",
    "shipping_not_supported": (
        "Ese cupón es de envío y el envío lo cobra la transportadora a su tarifa, "
        "sin descuentos: no se aplica."
    ),
}


def exhausted_text(code: str) -> str:
    return (
        f"Las unidades con descuento de {code} ya se agotaron. Ofrécele el precio "
        "normal con honestidad; no inventes otro descuento."
    )


@dataclass(frozen=True)
class CouponApplication:
    """Resultado de validar un código para el episodio."""

    code: str
    #: None = se puede aplicar. Si no: el motivo de `resolve_coupon`,
    #: "unavailable", "quota_exhausted" o "quota_unavailable".
    reason: str | None
    promotion: PromotionDTO | None = None
    #: Lo que recuerda la nota de cada turno (`eligible_products`).
    eligible: tuple[dict[str, Any], ...] = ()
    #: Cupo por unidad: las combinaciones que quedan (vacío = sin cupo).
    units: tuple[dict[str, Any], ...] = ()
    show_units_left: bool = True

    @property
    def applied(self) -> bool:
        return self.reason is None and self.promotion is not None

    @property
    def quota(self) -> bool:
        return bool(self.units)


async def resolve_coupon_application(
    code: str,
    *,
    promotions: Any,
    quotas: Any,
    sales: Any,
    catalog: Any,
    now_ms: int,
) -> CouponApplication:
    """Valida `code` contra Medusa y su cupo. No escribe nada: el que llama
    decide dónde guardarlo (`store_coupon_application`)."""
    normalized = normalize_coupon_code(code)
    try:
        active = await promotions.list_active()
        # Inactivas/vencidas también cuentan para explicar la razón.
        extra = await promotions.get_by_code(normalized)
        if extra is not None and all(p.id != extra.id for p in active):
            active = [*active, extra]
    except PromotionsUnavailableError:
        return CouponApplication(normalized, "unavailable")

    resolution = resolve_coupon(normalized, active, now_ms=now_ms)
    if not resolution.ok or resolution.promotion is None:
        return CouponApplication(normalized, resolution.reason or "not_found")

    promotion = resolution.promotion
    offer = await quota_offer(promotion, quotas=quotas, sales=sales, catalog=catalog)
    if offer.reason is not None:
        return CouponApplication(
            promotion.code, offer.reason, promotion, show_units_left=offer.show_units_left
        )
    if offer.has_quota:
        return CouponApplication(
            promotion.code,
            None,
            promotion,
            eligible=tuple(as_eligible(offer.units)),
            units=tuple(offer.units),
            show_units_left=offer.show_units_left,
        )
    eligible = await eligible_products(catalog, promotion)
    return CouponApplication(promotion.code, None, promotion, eligible=tuple(eligible))


class RecentSoldUnits:
    """Las vendidas del cupo leídas hace menos de `ttl_s`, compartidas entre
    las respuestas a una misma campaña: una campaña masiva no escanea los
    pedidos de Medusa una vez por cliente (lecturas simultáneas esperan la
    misma). Solo sirve para OFRECER: el registro las relee bajo el candado.
    Una lectura fallida no se recuerda; con `exclude` va directo a Medusa."""

    def __init__(
        self, reader: Any, *, ttl_s: float = 30.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._reader = reader
        self._ttl_s = ttl_s
        self._clock = clock
        self._lock = asyncio.Lock()
        self._recent: dict[Any, tuple[float, dict[str, int]]] = {}

    async def sold_units(self, *, since: Any, exclude: Any = None) -> dict[str, int]:
        if exclude:
            return await self._reader.sold_units(since=since, exclude=exclude)
        async with self._lock:
            hit = self._recent.get(since)
            if hit is not None and self._clock() - hit[0] < self._ttl_s:
                return dict(hit[1])
            sold = await self._reader.sold_units(since=since)
            self._recent[since] = (self._clock(), dict(sold))
            return dict(sold)


def store_coupon_application(
    metadata: dict[str, Any], application: CouponApplication, *, now_ms: int
) -> dict[str, Any]:
    """Mutates: fija el cupón en el episodio activo (lo crea si no hay)."""
    if application.promotion is None:
        raise ValueError("solo se guarda un cupón aplicado")
    return set_applied_coupon(
        metadata,
        promotion=application.promotion,
        now_ms=now_ms,
        eligible=list(application.eligible),
        quota=application.quota,
        units=list(application.units),
    )


__all__ = [
    "COUPON_REASON_TEXT",
    "CouponApplication",
    "TRANSIENT_REASONS",
    "exhausted_text",
    "resolve_coupon_application",
    "store_coupon_application",
]
