/**
 * Formatters plugin-locales del módulo Ads. La variante "K/M" abreviada y
 * `fmtPct` con decimales configurables son específicas del lenguaje visual
 * del dashboard publicitario; viven acá hasta que más de una feature las
 * necesite (§11 FSD).
 */

const COP = new Intl.NumberFormat("es-CO");

/** "$ 1.840.000" (sin abreviar — usado en filas de inspector y tabla). */
export function fmtMoney(n: number): string {
  return "$" + COP.format(n);
}

/** "$1.84M" / "$924K" / "$840" — denso, para KPI cards y barras del embudo. */
export function fmtMoneyK(n: number): string {
  if (n >= 1_000_000) return "$" + (n / 1_000_000).toFixed(2) + "M";
  if (n >= 1_000) return "$" + (n / 1_000).toFixed(0) + "K";
  return "$" + n;
}

/** "12.5%" / "2.24%" — `d` controla decimales (default 1). */
export function fmtPct(n: number, d = 1): string {
  return (n * 100).toFixed(d) + "%";
}

/** "184.320" — números enteros con separador de miles es-CO. */
export function fmtN(n: number): string {
  return COP.format(n);
}
