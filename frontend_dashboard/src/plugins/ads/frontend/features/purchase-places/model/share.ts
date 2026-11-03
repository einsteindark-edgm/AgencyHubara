/**
 * Cuentas de «Dónde compran» que comparten las dos vistas: etiqueta de
 * compras y participación de una parte en el total.
 */
import { fmtN } from "@plugins/ads/frontend/lib/format";

/** "1 compra" / "12 compras". */
export function purchases(n: number): string {
  return `${fmtN(n)} ${n === 1 ? "compra" : "compras"}`;
}

/** Parte sobre el todo (0..1); null sin total. */
export function shareOf(part: number, whole: number | null): number | null {
  if (!whole) return null;
  return part / whole;
}

/** "75 %" (entero); "—" sin total. */
export function fmtShare(share: number | null): string {
  return share === null ? "—" : `${Math.round(share * 100)} %`;
}
