/**
 * Modelo de dominio de un cupón/promoción de Medusa (camelCase).
 */

export interface Promotion {
  code: string;
  /** "percentage" | "fixed" | "buyget" */
  discountType: string;
  value: number;
  /** "items" | "order" | "shipping_methods" */
  targetType: string;
  name: string | null;
  endsAtMs: number | null;
  minSubtotalCop: number | null;
  /** 0 = aplica a todo el catálogo. */
  productCount: number;
}

export interface PromotionsInfo {
  promotions: Promotion[];
  /** Medusa no respondió: la lista está vacía por eso, no porque no haya. */
  unavailable: boolean;
}

/** Forma válida de un cupón — espejo de `COUPON_CODE_RE` del backend (solo
 *  letras y números: `VELAS_10` colisiona con el guard anti-leak del bot). */
export function sanitizeCouponCode(raw: string): string {
  return raw.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 14);
}

/** "15%" / "$5.000" / "15% en el envío" — para el selector del builder. */
export function promotionLabel(p: Promotion): string {
  let label =
    p.discountType === "percentage"
      ? `${p.value}%`
      : p.discountType === "fixed"
        ? `$${p.value.toLocaleString("es-CO")}`
        : "promoción";
  if (p.targetType === "shipping_methods") label += " en el envío";
  if (p.productCount > 0) label += " · productos seleccionados";
  return label;
}
