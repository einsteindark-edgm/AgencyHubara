/**
 * Normaliza el valor del envío que escribe el operador en el modal "En camino".
 *
 *  - trim; vacío o "0" → `{ value: null }` (= "marcar en camino sin valor");
 *  - acepta "12000", "12.000" (miles con punto) y "$ 12.000";
 *  - decimales ("12,5"), negativos, texto o montos absurdos (> 10.000.000 COP)
 *    → `{ error }` (el modal lo muestra y no confirma).
 *
 * Espejo del validador del backend (`_parse_shipping_cost` en la orders API):
 * el valor viaja como entero COP, sin decimales.
 */
export const SHIPPING_COST_MAX = 10_000_000;

export type ShippingCostParse = { value: number | null } | { error: string };

const INVALID = "El valor del envío debe ser un monto en pesos, sin decimales (ej. 12.000).";

export function parseShippingCost(raw: string): ShippingCostParse {
  const trimmed = raw.trim().replace(/^\$\s*/, "");
  if (!trimmed) return { value: null };
  if (!/^\d{1,3}(\.\d{3})*$|^\d+$/.test(trimmed)) return { error: INVALID };
  const value = Number(trimmed.replace(/\./g, ""));
  if (!Number.isSafeInteger(value) || value < 0) return { error: INVALID };
  if (value > SHIPPING_COST_MAX) {
    return { error: `El valor del envío no puede superar $ ${SHIPPING_COST_MAX.toLocaleString("es-CO")}.` };
  }
  return { value: value || null };
}
