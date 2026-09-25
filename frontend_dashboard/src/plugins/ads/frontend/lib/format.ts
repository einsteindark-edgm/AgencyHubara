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

/** "US$0.0018" — costo LLM en USD (sub-centavo). 4 decimales para que el
 *  micro-costo sea legible; sube a 2 decimales cuando supera 1¢. "US$0" si 0.
 *  Etiquetado "US$" para distinguir del COP de las ventas. */
export function fmtUsd(n: number): string {
  if (!n) return "US$0";
  if (n < 0.01) return "US$" + n.toFixed(4);
  return "US$" + n.toFixed(2);
}

/** "US$0.0008" / "US$3.27" — costo de WhatsApp desde USD micros (1e-6 USD,
 *  enteros). Un mensaje de servicio en Colombia vale US$0.0008: con 2
 *  decimales toda conversación se leería "US$0.00" — por debajo de US$1 van 4
 *  decimales; de US$1 en adelante, 2. "US$" para no confundirlo con el COP. */
export function fmtUsdMicros(micros: number): string {
  if (!micros) return "US$0";
  const usd = micros / 1_000_000;
  return "US$" + usd.toFixed(usd < 1 ? 4 : 2);
}

/** "1h 12m" / "45m" / "38s" — duración legible desde milisegundos. Para el
 *  "tiempo" del embudo (duración de episodio). "—" si null/0/negativo. */
export function fmtDuration(ms: number): string {
  if (!ms || ms < 0) return "—";
  const totalMin = Math.round(ms / 60000);
  if (totalMin < 1) return Math.round(ms / 1000) + "s";
  if (totalMin < 60) return totalMin + "m";
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
}

/** Tasa APROXIMADA USD→COP del dashboard para comparar el costo de WhatsApp
 *  (USD) con las ventas (COP): la misma que usa la sección Marketing (sus
 *  costos "US$ + COP aprox."). Los plugins no comparten código de frontend
 *  (P-22), por eso vive duplicada acá. */
export const USD_TO_COP_APPROX = 4000;
