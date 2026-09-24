"""Dominio PURO del cupón de la central (Marketing → Cupones).

`CouponSpec` es lo que llena el operador; `coupon_to_medusa_payload` lo
traduce a `POST /admin/promotions` (promoción + campaña en UNA llamada).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

#: Las fechas del cupón son días de Colombia (sin horario de verano, pero se
#: resuelve con la zona igual).
BOGOTA = ZoneInfo("America/Bogota")

#: Código de la central: 3–14 letras o dígitos. Dentro de `COUPON_CODE_RE`
#: (sin `_`, colisión con el guard anti-leak) y del tope de 14 caracteres de
#: la plantilla de campaña de WhatsApp.
CENTRAL_CODE_RE = re.compile(r"[A-Z0-9]{3,14}")
CAMPAIGN_NAME_MAX = 80
NEW_COUPON_STATUSES = ("draft", "active")
#: El único atributo de regla que la central escribe (y el que el panel de
#: Medusa muestra; `items.variant.id` no lo muestra).
PRODUCT_RULE_ATTR = "items.product.id"


@dataclass(frozen=True)
class CouponSpec:
    code: str
    campaign_name: str
    percentage: int
    products: tuple[str, ...] | None
    starts_on: date
    ends_on: date
    status: str = "active"


class CouponSpecError(ValueError):
    """Dato inválido del formulario: `field` + mensaje para el operador."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


def _parse_code(raw: Any) -> str:
    code = str(raw or "").strip().upper()
    if not CENTRAL_CODE_RE.fullmatch(code):
        raise CouponSpecError(
            "code", "El código lleva de 3 a 14 letras o números, sin espacios ni símbolos."
        )
    return code


def _parse_percentage(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 100:
        raise CouponSpecError(
            "percentage", "El descuento es un número entero entre 1 y 100."
        )
    return raw


def _parse_products(raw: Any) -> tuple[str, ...] | None:
    if raw == "all":
        return None
    if not isinstance(raw, (list, tuple)):
        raise CouponSpecError("products", 'Elige productos o "todo el catálogo".')
    ids = tuple(dict.fromkeys(str(p).strip() for p in raw if str(p or "").strip()))
    if not ids:
        raise CouponSpecError("products", 'Elige al menos un producto o "todo el catálogo".')
    return ids


def _parse_day(field: str, raw: Any) -> date:
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw or "").strip())
    except ValueError:
        raise CouponSpecError(field, "La fecha va como AAAA-MM-DD.") from None


def parse_coupon_spec(raw: Any) -> CouponSpec:
    """Formulario de la central → `CouponSpec` validado.

    Levanta `CouponSpecError(field, message)` con el primer dato inválido; el
    mensaje le dice al operador cómo corregirlo.
    """
    if not isinstance(raw, dict):
        raise CouponSpecError("body", "Faltan los datos del cupón.")
    code = _parse_code(raw.get("code"))
    name = str(raw.get("campaign_name") or "").strip() or code
    if len(name) > CAMPAIGN_NAME_MAX:
        raise CouponSpecError(
            "campaign_name", f"El nombre de la campaña va hasta {CAMPAIGN_NAME_MAX} caracteres."
        )
    percentage = _parse_percentage(raw.get("percentage"))
    products = _parse_products(raw.get("products"))
    starts_on = _parse_day("starts_on", raw.get("starts_on"))
    ends_on = _parse_day("ends_on", raw.get("ends_on"))
    if ends_on < starts_on:
        raise CouponSpecError("ends_on", 'El "hasta" no puede ser antes del "desde".')
    status = str(raw.get("status") or "active")
    if status not in NEW_COUPON_STATUSES:
        raise CouponSpecError("status", "Un cupón nuevo se crea como borrador o activo.")
    return CouponSpec(
        code=code,
        campaign_name=name,
        percentage=percentage,
        products=products,
        starts_on=starts_on,
        ends_on=ends_on,
        status=status,
    )


def day_start_utc(day: date) -> str:
    """00:00 de ese día en Bogotá, en UTC ISO (`2026-09-22T05:00:00Z`)."""
    local = datetime.combine(day, time.min, tzinfo=BOGOTA)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _product_rules(products: tuple[str, ...] | None) -> list[dict[str, Any]]:
    """Sin productos = todo el catálogo = sin reglas."""
    if not products:
        return []
    return [{"attribute": PRODUCT_RULE_ATTR, "operator": "in", "values": list(products)}]


def coupon_to_medusa_payload(spec: CouponSpec) -> dict[str, Any]:
    """`POST /admin/promotions` con la campaña en línea (atómico).

    `across` sin `max_quantity` (Medusa lo prohíbe con `across`; el tope por
    unidad lo pone el cupo de Hubara) y sin presupuesto (no se mueve con
    draft orders). El "hasta" es inclusivo: la campaña termina a las 00:00
    del día SIGUIENTE, hora de Bogotá.
    """
    method: dict[str, Any] = {
        "type": "percentage",
        "value": spec.percentage,
        "target_type": "items",
        "allocation": "across",
        "target_rules": _product_rules(spec.products),
    }
    return {
        "code": spec.code,
        "type": "standard",
        "is_automatic": False,
        "status": spec.status,
        "application_method": method,
        "campaign": {
            "name": spec.campaign_name,
            "campaign_identifier": spec.code,
            "starts_at": day_start_utc(spec.starts_on),
            "ends_at": day_start_utc(spec.ends_on + timedelta(days=1)),
        },
    }


@dataclass(frozen=True)
class CouponView:
    promotion_id: str
    campaign_id: str | None
    code: str
    campaign_name: str | None
    percentage: int | None
    products: tuple[str, ...] | None
    starts_on: date | None
    ends_on: date | None
    status: str
    state: str
    manageable: bool
    unmanageable_reason: str | None


#: Estados que ve el operador (derivados de `status` + fechas + `now`).
STATE_DRAFT = "draft"
STATE_SCHEDULED = "scheduled"
STATE_ACTIVE = "active"
STATE_PAUSED = "paused"
STATE_EXPIRED = "expired"

_TAG_ATTRS = ("items.product.tags", "product.tags")


def _rule_values(rule: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for v in rule.get("values") or []:
        if isinstance(v, dict):
            v = v.get("value")
        if v is not None and str(v).strip():
            out.append(str(v).strip())
    return out


def _parse_instant(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _first_day(instant: datetime | None) -> date | None:
    return instant.astimezone(BOGOTA).date() if instant else None


def _last_day(instant: datetime | None) -> date | None:
    """El "hasta" inclusivo: una campaña que cierra a las 00:00 Bogotá tiene
    como último día el anterior; una que cierra a media jornada, ese día."""
    if instant is None:
        return None
    local = instant.astimezone(BOGOTA)
    return local.date() - timedelta(days=1) if local.time() == time.min else local.date()


def _unmanageable_reason(raw: dict[str, Any], method: dict[str, Any]) -> str | None:
    """Por qué la central NO puede editar esta promoción (D7), o None.

    La central solo escribe lo que ella misma crea: porcentaje sobre
    productos, a lo sumo UNA regla `items.product.id in`, sin condiciones de
    compra y con campaña. Todo lo demás queda en solo lectura: ninguna
    escritura toca reglas ajenas.
    """
    if raw.get("is_automatic"):
        return "Es una promoción automática (sin código); se maneja en Medusa."
    if str(raw.get("type") or "standard") != "standard":
        return 'Es una promoción de tipo "compra y lleva"; se maneja en Medusa.'
    if str(method.get("type") or "") != "percentage":
        return "Es de monto fijo; la central maneja solo cupones de porcentaje."
    target = str(method.get("target_type") or "")
    if target == "shipping_methods":
        return "Descuenta el envío; la central maneja solo descuentos a productos."
    if target != "items":
        return "Descuenta el pedido completo; la central maneja solo descuentos a productos."
    rules = [r for r in method.get("target_rules") or [] if isinstance(r, dict)]
    if any(str(r.get("attribute") or "").startswith(_TAG_ATTRS) for r in rules):
        return "Tiene una condición por etiquetas creada en Medusa; se ve en solo lectura."
    if len(rules) > 1 or any(
        r.get("attribute") != PRODUCT_RULE_ATTR or r.get("operator") != "in" for r in rules
    ):
        return "Tiene condiciones que la central no maneja; se ve en solo lectura."
    if any(isinstance(r, dict) for r in raw.get("rules") or []):
        return "Tiene condiciones de compra (por ejemplo, mínimo de compra); se maneja en Medusa."
    if not isinstance(raw.get("campaign"), dict):
        return "No tiene campaña (sin fechas); se maneja en Medusa."
    return None


def _state(status: str, starts: datetime | None, ends: datetime | None, now: datetime) -> str:
    if ends is not None and ends <= now:
        return STATE_EXPIRED
    if status == "draft":
        return STATE_DRAFT
    if status != "active":
        return STATE_PAUSED
    if starts is not None and now < starts:
        return STATE_SCHEDULED
    return STATE_ACTIVE


def coupon_view_from_medusa(raw: dict[str, Any], *, now: datetime) -> CouponView:
    """Promoción de `GET /admin/promotions` → lo que muestra la central.

    Siempre refleja lo que HAY en Medusa (aunque lo hayan editado en Medusa
    Admin) y dice si la central puede editarlo (`manageable`).
    """
    raw_method = raw.get("application_method")
    method: dict[str, Any] = raw_method if isinstance(raw_method, dict) else {}
    raw_campaign = raw.get("campaign")
    campaign: dict[str, Any] | None = raw_campaign if isinstance(raw_campaign, dict) else None
    starts = _parse_instant((campaign or {}).get("starts_at"))
    ends = _parse_instant((campaign or {}).get("ends_at"))
    product_rules = [
        r
        for r in method.get("target_rules") or []
        if isinstance(r, dict) and r.get("attribute") == PRODUCT_RULE_ATTR
    ]
    products = tuple(v for r in product_rules for v in _rule_values(r)) or None
    reason = _unmanageable_reason(raw, method)
    is_percentage = str(method.get("type") or "") == "percentage"
    value = method.get("value")
    status = str(raw.get("status") or "active")
    return CouponView(
        promotion_id=str(raw.get("id") or ""),
        campaign_id=(campaign or {}).get("id") or raw.get("campaign_id") or None,
        code=str(raw.get("code") or "").strip().upper(),
        campaign_name=(campaign or {}).get("name") or None,
        percentage=int(round(float(value))) if is_percentage and value is not None else None,
        products=products,
        starts_on=_first_day(starts),
        ends_on=_last_day(ends),
        status=status,
        state=_state(status, starts, ends, now),
        manageable=reason is None,
        unmanageable_reason=reason,
    )
