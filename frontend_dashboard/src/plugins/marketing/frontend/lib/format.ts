/**
 * Formatters plugin-locales de marketing (§11 FSD: viven acá hasta que otro
 * plugin los necesite).
 *
 * Unidades: el costo por mensaje de WhatsApp viaja en **USD micros**
 * (cost_unit_lesson: pricing sub-cent jamás se modela en cents) y las ventas
 * atribuidas en **COP**. La conversión USD→COP es una tasa fija aproximada,
 * SOLO para dar orden de magnitud al operador — siempre etiquetada "aprox".
 */

import { ApiError } from "@/shared/sdk";

const INT_CO = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 0 });
const USD_CO_2 = new Intl.NumberFormat("es-CO", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const USD_CO_4 = new Intl.NumberFormat("es-CO", {
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
});

/** "US$2,50" (2 decimales) / "US$0,0125" (4 decimales para el costo unitario
 *  sub-cent). Formato es-CO: coma decimal. */
export function fmtUsdMicros(micros: number, decimals: 2 | 4 = 2): string {
  const nf = decimals === 4 ? USD_CO_4 : USD_CO_2;
  return "US$" + nf.format(micros / 1_000_000);
}

/** "$1.840.000" — COP con puntos de miles es-CO. */
export function fmtCop(n: number): string {
  return "$" + INT_CO.format(n);
}

/** "1.234" — enteros con separador de miles es-CO. */
export function fmtN(n: number): string {
  return INT_CO.format(n);
}

/** Tasa fija aproximada USD→COP para estimaciones de costo (sufijo "aprox"
 *  obligatorio en la UI — la verdad la trae el webhook de pricing de Meta). */
export const USD_TO_COP_APPROX = 4000;

export function usdMicrosToCop(
  micros: number,
  rate: number = USD_TO_COP_APPROX,
): number {
  return Math.round((micros / 1_000_000) * rate);
}

/** "12 jul, 14:30" — timestamp corto es-CO para historial de pruebas. */
export function fmtDateTimeMs(ms: number): string {
  const d = new Date(ms);
  const date = d.toLocaleDateString("es-CO", { day: "numeric", month: "short" });
  const time = d.toLocaleTimeString("es-CO", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return `${date}, ${time}`;
}

/**
 * Mensaje legible de un error de mutación. Superficie el `detail` de FastAPI
 * (p.ej. el 404 de test-send "el número no tiene conversación previa…", o el
 * 422 de send con la lista de faltantes) — nunca un "[object Object]".
 */
export function apiErrorDetail(err: unknown): string {
  if (err instanceof ApiError) {
    const body = err.body;
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail?: unknown }).detail;
      if (typeof detail === "string" && detail) return detail;
      if (detail !== undefined) return JSON.stringify(detail);
    }
    return `Error ${err.status}`;
  }
  if (err instanceof Error) return err.message;
  return String(err);
}
