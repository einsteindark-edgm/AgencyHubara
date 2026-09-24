/**
 * Cotización del cupón en "Crear pedido": lo que el operador VIO del cupón
 * antes de registrar (D1/D4 del premortem de la central de cupones).
 *
 * El registro manda ESE descuento como `expected_discount_cop`: si el backend
 * calcula otro (se vendieron unidades mientras tanto), responde
 * `quota_changed` con los montos nuevos y el formulario los ADOPTA — el
 * siguiente clic registra contra lo que el operador acaba de ver, en vez de
 * repetir el descuento viejo para siempre. Si el operador edita las líneas,
 * la cotización queda vieja y el siguiente clic la recalcula sin registrar
 * (`dry_run`, C-1) antes de que se le cobre al cliente un total que nadie vio.
 */

import type {
  CreateOrderResult,
  OrderSuggestion,
} from "@plugins/chats/frontend/entities/order-intake";

export interface CouponQuote {
  code: string;
  discountCop: number;
  /** Por qué el descuento es 0 o parcial (`coupon_reason`); null = aplica. */
  reason: string | null;
  /** `suggestion` = trae el reparto por línea de la sugerencia; `backend` =
   *  recalculado (cálculo sin registrar o `quota_changed`), sin reparto. */
  source: "suggestion" | "backend";
  /** Montos del backend. La sugerencia no los muestra: su envío sale de la
   *  ciudad sugerida y el total real se fija al registrar. */
  totals: { subtotalCop: number; shippingCop: number; totalCop: number } | null;
}

/** El cupón aplicado en el chat — también a $0 (C-2); null = sin cupón. */
export function quoteFromSuggestion(suggestion: OrderSuggestion): CouponQuote | null {
  if (!suggestion.coupon_code) return null;
  return {
    code: suggestion.coupon_code,
    discountCop: suggestion.discount_cop,
    reason: suggestion.coupon_reason,
    source: "suggestion",
    totals: null,
  };
}

/** Los montos que calculó el backend (cálculo sin registrar o `quota_changed`
 *  con montos); null si la respuesta no los trae — hay que recalcular. */
export function quoteFromResult(
  previous: CouponQuote,
  result: CreateOrderResult,
): CouponQuote | null {
  if (result.discount_cop === null || result.total_cop === null) return null;
  return {
    code: result.coupon_code ?? previous.code,
    discountCop: result.discount_cop,
    reason: null,
    source: "backend",
    totals:
      result.subtotal_cop !== null && result.shipping_cop !== null
        ? {
            subtotalCop: result.subtotal_cop,
            shippingCop: result.shipping_cop,
            totalCop: result.total_cop,
          }
        : null,
  };
}

const COUPON_REASON_LABEL: Record<string, string> = {
  missing_attributes: "falta elegir color y aroma",
  quota_exhausted: "se agotaron las unidades con descuento",
  quota_unavailable: "no se pudieron leer las unidades (intenta en un minuto)",
  min_subtotal: "el pedido no alcanza el mínimo del cupón",
  no_applicable_items: "ningún producto del pedido aplica para el cupón",
  unsupported: "este cupón no se puede aplicar desde aquí",
};

/** `coupon_reason` → frase para el operador; null si no hay (o no se conoce). */
export function couponReasonLabel(reason: string | null): string | null {
  if (!reason) return null;
  return COUPON_REASON_LABEL[reason] ?? null;
}
