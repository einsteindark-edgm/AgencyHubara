/**
 * Formatters plugin-locales de marketing (§11 FSD: viven acá hasta que otro
 * plugin los necesite).
 *
 * Unidades: el costo por mensaje de WhatsApp viaja en **USD micros**
 * (cost_unit_lesson: pricing sub-cent jamás se modela en cents) y las ventas
 * atribuidas en **COP**. La conversión USD→COP es una tasa fija aproximada,
 * SOLO para dar orden de magnitud al operador — siempre etiquetada "aprox".
 */

import { BOGOTA_TZ } from "@/shared/lib";
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

/* Fechas SIEMPRE en hora de Colombia (D14): el navegador del operador puede
 * estar en otra zona (VPN, viaje) y "cuándo" es el de la tienda. */
const DAY_MONTH = new Intl.DateTimeFormat("es-CO", {
  timeZone: BOGOTA_TZ,
  day: "numeric",
  month: "short",
});
const DAY_MONTH_YEAR = new Intl.DateTimeFormat("es-CO", {
  timeZone: BOGOTA_TZ,
  day: "numeric",
  month: "short",
  year: "numeric",
});
const HOUR_MINUTE = new Intl.DateTimeFormat("es-CO", {
  timeZone: BOGOTA_TZ,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const YEAR = new Intl.DateTimeFormat("en-CA", { timeZone: BOGOTA_TZ, year: "numeric" });

/** Fecha con año ("22 de sept de 2025") — para registros que se miran meses
 *  después (bajas), donde el día y mes solos son ambiguos. */
export function fmtDateMs(ms: number): string {
  return DAY_MONTH_YEAR.format(new Date(ms));
}

/** "23 de sept, 22:30" — timestamp corto (historial de pruebas, registro de
 *  cambios, ventas del cupón); con el año si no es el año en curso. `nowMs`
 *  se lee en render (regla 5): no llamarla desde un mapper ni un queryFn. */
export function fmtDateTimeMs(ms: number, nowMs: number = Date.now()): string {
  const d = new Date(ms);
  const sameYear = YEAR.format(d) === YEAR.format(new Date(nowMs));
  const date = (sameYear ? DAY_MONTH : DAY_MONTH_YEAR).format(d);
  // Algunos ICU emiten "24:05" para la medianoche con hour12:false.
  const time = HOUR_MINUTE.format(d).replace(/^24:/, "00:");
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
      // Central de cupones: `{message, field?|rows?|step?}` — el message ya
      // está escrito para el operador.
      if (detail && typeof detail === "object" && "message" in detail) {
        const message = (detail as { message?: unknown }).message;
        if (typeof message === "string" && message) return message;
      }
      if (detail !== undefined) return JSON.stringify(detail);
    }
    return `Error ${err.status}`;
  }
  if (err instanceof Error) return err.message;
  return String(err);
}
