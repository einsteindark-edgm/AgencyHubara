"""Tools de cupones del bot de ventas: `list_promotions` + `apply_coupon`.

Las promociones viven en Medusa (Admin → Promotions). Acá el bot solo las
LEE: qué cupones hay y a qué productos aplican, y valida el código que da el
cliente. El monto lo calcula el sistema (use case `coupons`), nunca el LLM.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from exoclaw.agent.tools import ToolBase, ToolContext
from loguru import logger

from src.sdk.connectorkit import (
    PromotionDTO,
    PromotionsPort,
    PromotionsUnavailableError,
    compute_discount,
    normalize_coupon_code,
    resolve_coupon,
)

from src.plugins.chats.agent.sales.use_cases.coupon_quota import (
    as_eligible,
    quota_offer,
    units_text,
)
from src.plugins.chats.agent.sales.use_cases.coupons import (
    clear_applied_coupon,
    describe_promotion,
    discount_line_items,
    eligible_products,
    eligible_products_text,
    format_cop,
    is_whole_catalog,
    set_applied_coupon,
)

_REASON_TEXT = {
    "invalid_format": "El código no tiene una forma válida (solo letras y números, sin guiones ni espacios).",
    "not_found": "Ese código no existe.",
    "inactive": "Ese cupón ya no está activo.",
    "not_started": "Ese cupón todavía no empieza a regir.",
    "expired": "Ese cupón ya venció.",
    "budget_exhausted": "Ese cupón ya se agotó.",
    "unavailable": "No pude validar el cupón ahora mismo (sistema de promociones caído).",
    "scope_unresolved": "No pude confirmar a qué productos aplica ese cupón, así que no lo apliqué.",
    "quota_unavailable": "No pude confirmar cuántas unidades con descuento quedan, así que no apliqué el cupón.",
}


def _exhausted_text(code: str) -> str:
    return (
        f"Las unidades con descuento de {code} ya se agotaron. Ofrécele el precio "
        "normal con honestidad; no inventes otro descuento."
    )


async def _product_titles(catalog: Any, promotion: PromotionDTO) -> list[str] | str:
    """Nombres de los productos a los que aplica, o "todo el catálogo"."""
    if is_whole_catalog(promotion):
        return "todo el catálogo"
    return [p["title"] for p in await eligible_products(catalog, promotion)]


def _promotion_summary(p: dict[str, Any]) -> str:
    if p.get("exhausted"):
        return f"{p['code']} (agotado: ya no quedan unidades con descuento)"
    if p.get("units_unavailable"):
        return f"{p['code']} (no pude confirmar cuántas unidades quedan; no lo ofrezcas)"
    if "units" in p:
        return f"{p['code']} ({p['discount']}, SOLO en: {units_text(p['units'])})"
    scope = (
        "todo el catálogo"
        if p["products"] == "todo el catálogo"
        else ", ".join(p["products"]) or "productos seleccionados"
    )
    return f"{p['code']} ({p['discount']}, {scope})"


class ListPromotionsTool(ToolBase):
    name = "list_promotions"
    description = (
        "Lista los cupones/promociones VIGENTES (código, descuento, productos a "
        "los que aplica, vigencia). Úsala cuando el cliente pregunta si hay "
        "descuentos o promociones. Si devuelve vacío, NO hay promociones: dilo "
        "y no inventes ninguna; si el cliente insiste en negociar un precio, "
        "`escalate_to_human(reason_category='DISCOUNT_REQUEST')`."
    )
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(
        self,
        workspace: str | Path,
        *,
        promotions: PromotionsPort,
        catalog: Any = None,
        quotas: Any = None,
        sales: Any = None,
    ) -> None:
        """`quotas`/`sales`: cupo por unidad (almacén + lector de vendidas del
        SDK). Sin ellos, los cupones se listan como siempre."""
        self._workspace = Path(workspace)
        self._promotions = promotions
        self._catalog = catalog
        self._quotas = quotas
        self._sales = sales

    async def execute_with_context(self, ctx: ToolContext) -> str:
        try:
            promotions = await self._promotions.list_active()
        except PromotionsUnavailableError as exc:
            logger.warning("🎟️ [TOOL list_promotions] unavailable session={} err={}", ctx.session_key, exc)
            return json.dumps(
                {
                    "promotions": [],
                    "error": "unavailable",
                    "summary": "No pude consultar las promociones ahora mismo. No inventes ninguna.",
                },
                ensure_ascii=False,
            )
        out = []
        for promo in promotions:
            if promo.scope_unresolved:
                # Reglas ilegibles: no sabemos a qué aplica — no se ofrece.
                continue
            entry: dict[str, Any] = {
                "code": promo.code,
                "discount": describe_promotion(promo),
                "products": await _product_titles(self._catalog, promo),
                "min_subtotal_cop": promo.min_subtotal_cop,
                "ends_at_ms": promo.ends_at_ms,
                "name": promo.description,
            }
            offer = await quota_offer(promo, quotas=self._quotas, sales=self._sales, catalog=self._catalog)
            if offer.has_quota:
                # Cupo por unidad: el cupón vale SOLO en estas combinaciones.
                entry["units"] = list(offer.units)
                entry["products"] = [u["title"] for u in offer.units]
                if offer.reason is not None:
                    entry["exhausted"] = offer.reason == "quota_exhausted"
                    entry["units_unavailable"] = offer.reason == "quota_unavailable"
            out.append(entry)
        if not out:
            summary = "No hay promociones ni cupones vigentes. No prometas descuentos."
        else:
            summary = "Cupones vigentes: " + "; ".join(
                _promotion_summary(p) for p in out
            ) + ". El cliente lo aplica dándote el código → `apply_coupon`."
        return json.dumps({"promotions": out, "summary": summary}, ensure_ascii=False)


class ApplyCouponTool(ToolBase):
    name = "apply_coupon"
    description = (
        "Valida y aplica al pedido el CÓDIGO DE CUPÓN que dio el cliente. "
        "Llámala apenas el cliente mencione un código (ej. 'tengo el cupón "
        "MAMA15'). Devuelve `applied` y, si pasaste `items`, el `discount_cop` "
        "estimado. El descuento real lo calcula el sistema en "
        "`present_order_confirmation` y `register_order` — usa el total que "
        "devuelven esos envelopes. Si `applied=false`, dile al cliente la "
        "razón con honestidad; NUNCA apliques un descuento por tu cuenta. "
        "Para quitar el cupón, llámala con `code=''`."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "maxLength": 40,
                "description": "Código tal cual lo escribió el cliente (vacío = quitar el cupón).",
            },
            "items": {
                "type": "array",
                "description": "Opcional: ítems actuales para estimar el descuento.",
                "items": {
                    "type": "object",
                    "properties": {
                        "handle": {"type": "string"},
                        "quantity": {"type": "integer", "minimum": 1},
                        "unit_price_cop": {"type": "integer", "minimum": 0},
                    },
                    "required": ["handle", "quantity", "unit_price_cop"],
                },
            },
        },
        "required": ["code"],
    }

    def __init__(
        self,
        workspace: str | Path,
        *,
        promotions: PromotionsPort,
        metadata_store: Any,
        catalog: Any = None,
        now_ms: Callable[[], int] | None = None,
        quotas: Any = None,
        sales: Any = None,
    ) -> None:
        """`metadata_store`: store de `metadata.json` del vault (DI desde la
        composición — `build_session_metadata_store`; las tools no importan
        platform ni el runtime del SDK, que arrastra temporalio: R-DIP).
        `quotas`/`sales`: cupo por unidad (sin ellos, como siempre)."""
        self._workspace = Path(workspace)
        self._promotions = promotions
        self._catalog = catalog
        self._store = metadata_store
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._quotas = quotas
        self._sales = sales

    async def execute_with_context(
        self, ctx: ToolContext, code: str, items: list[dict[str, Any]] | None = None
    ) -> str:
        store = self._store
        normalized = normalize_coupon_code(code)
        if not normalized:
            removed = {"removed": False}

            def _clear(md: dict[str, Any]) -> dict[str, Any]:
                removed["removed"] = clear_applied_coupon(md)
                return md

            store.update(ctx.session_key, _clear)
            return json.dumps(
                {
                    "applied": False,
                    "reason": "removed",
                    "summary": "Cupón quitado del pedido."
                    if removed["removed"]
                    else "No había ningún cupón aplicado.",
                },
                ensure_ascii=False,
            )

        try:
            promotions = await self._promotions.list_active()
            # Inactivas/vencidas también cuentan para explicar la razón.
            extra = await self._promotions.get_by_code(normalized)
            if extra is not None and all(p.id != extra.id for p in promotions):
                promotions = [*promotions, extra]
        except PromotionsUnavailableError as exc:
            logger.warning("🎟️ [TOOL apply_coupon] unavailable session={} err={}", ctx.session_key, exc)
            return json.dumps(
                {"applied": False, "code": normalized, "reason": "unavailable",
                 "summary": _REASON_TEXT["unavailable"] + " Pídele al cliente que lo intente en un momento."},
                ensure_ascii=False,
            )

        now_ms = self._now_ms()
        resolution = resolve_coupon(normalized, promotions, now_ms=now_ms)
        if not resolution.ok or resolution.promotion is None:
            reason = resolution.reason or "not_found"
            logger.info("🎟️ [TOOL apply_coupon] rejected session={} code={} reason={}", ctx.session_key, normalized, reason)
            return json.dumps(
                {
                    "applied": False,
                    "code": normalized,
                    "reason": reason,
                    "summary": _REASON_TEXT.get(reason, "Ese cupón no se puede aplicar.")
                    + " Díselo al cliente con honestidad; no apliques ningún descuento.",
                },
                ensure_ascii=False,
            )

        promotion = resolution.promotion
        offer = await quota_offer(
            promotion, quotas=self._quotas, sales=self._sales, catalog=self._catalog
        )
        if offer.reason is not None:
            logger.info(
                "🎟️ [TOOL apply_coupon] rejected session={} code={} reason={}",
                ctx.session_key, normalized, offer.reason,
            )
            text = (
                _exhausted_text(promotion.code)
                if offer.reason == "quota_exhausted"
                else _REASON_TEXT[offer.reason] + " Díselo al cliente con honestidad."
            )
            return json.dumps(
                {"applied": False, "code": promotion.code, "reason": offer.reason, "summary": text},
                ensure_ascii=False,
            )
        if offer.has_quota:
            return self._applied_with_quota(ctx, promotion, offer, now_ms)
        whole_catalog = is_whole_catalog(promotion)
        eligible = await eligible_products(self._catalog, promotion)
        store.update(
            ctx.session_key,
            lambda md: set_applied_coupon(
                md, promotion=promotion, now_ms=now_ms, eligible=eligible
            ),
        )
        logger.info("🎟️ [TOOL apply_coupon] applied session={} code={}", ctx.session_key, promotion.code)

        envelope: dict[str, Any] = {
            "applied": True,
            "code": promotion.code,
            "discount": describe_promotion(promotion),
            "whole_catalog": whole_catalog,
            "eligible_products": eligible,
            "min_subtotal_cop": promotion.min_subtotal_cop,
        }
        summary = f"Cupón {promotion.code} aplicado: {describe_promotion(promotion)}"
        if whole_catalog:
            summary += " en todo el catálogo"
        elif eligible:
            summary += (
                f" SOLO en: {eligible_products_text(eligible)}. Muéstrale y ofrécele "
                "ESTOS productos; lo que hablaron antes de otros productos va SIN "
                "descuento (retómalo solo si el cliente lo pide, aclarándolo)"
            )
        elif promotion.target_type != "shipping_methods":
            summary += (
                " solo en algunos productos que no pude identificar en el catálogo; "
                "no prometas descuento en un producto concreto"
            )
        if items:
            lines = await discount_line_items(self._catalog, items)
            result = compute_discount(promotion, lines)
            envelope["discount_cop"] = result.discount_cop
            envelope["applicable_handles"] = list(result.applicable_handles)
            if result.reason == "min_subtotal":
                summary += (
                    f" — requiere una compra mínima de {format_cop(result.min_subtotal_cop or 0)}"
                    " en productos; con este pedido NO descuenta todavía"
                )
            elif result.reason == "no_applicable_items":
                summary += " — no aplica a los productos de este pedido (sigue guardado por si agrega otro)"
            else:
                summary += f" → descuento estimado {format_cop(result.discount_cop)}"
        summary += (
            ". El total final lo calcula `present_order_confirmation`/`register_order`; "
            "no prometas otro monto."
        )
        envelope["summary"] = summary
        return json.dumps(envelope, ensure_ascii=False)

    def _applied_with_quota(
        self, ctx: ToolContext, promotion: PromotionDTO, offer: Any, now_ms: int
    ) -> str:
        """Cupón con cupo: vale SOLO en las combinaciones que quedan. El
        reparto real (qué unidades del pedido llevan descuento) lo hacen
        `present_order_confirmation`/`register_order`."""
        eligible = as_eligible(offer.units)
        self._store.update(
            ctx.session_key,
            lambda md: set_applied_coupon(md, promotion=promotion, now_ms=now_ms, eligible=eligible, quota=True),
        )
        logger.info("🎟️ [TOOL apply_coupon] applied (cupo) session={} code={}", ctx.session_key, promotion.code)
        summary = (
            f"Cupón {promotion.code} aplicado: {describe_promotion(promotion)} SOLO en estas "
            f"unidades: {units_text(offer.units)}. Otros colores, aromas o productos van a "
            "precio normal. Para aplicarlo necesitas el color y el aroma de cada producto"
        )
        if not offer.show_units_left:
            summary += "; no le digas cuántas quedan"
        summary += (
            ". El total final lo calcula `present_order_confirmation`/`register_order`; "
            "no prometas otro monto."
        )
        return json.dumps(
            {
                "applied": True,
                "code": promotion.code,
                "discount": describe_promotion(promotion),
                "whole_catalog": False,
                "units": list(offer.units),
                "eligible_products": eligible,
                "min_subtotal_cop": promotion.min_subtotal_cop,
                "summary": summary,
            },
            ensure_ascii=False,
        )


__all__ = ["ApplyCouponTool", "ListPromotionsTool"]
