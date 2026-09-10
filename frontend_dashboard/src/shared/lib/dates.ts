/**
 * Helpers puros de fecha-calendario (YYYY-MM-DD) para filtros y defaults de
 * formularios. Centralizan el patrón `new Date(...).toISOString().slice(0,10)`
 * que estaba copiado en componentes (OrdersFilters, OrdersHeader,
 * ReadyForShip, ConfirmPaymentAction) — los componentes no deben hacer
 * aritmética de reloj inline (auditoría 2026-06-10, F0.4).
 *
 * Semántica: día calendario **America/Bogota**.
 *
 * Hasta 2026-09-10 estos helpers cortaban el día en **UTC** y existía un
 * `TODO(F5)` para evaluar el cambio. Se resolvió con el operador: el corte UTC
 * adelanta la frontera a las 19:00 hora local, así que cada noche a partir de
 * las 7 "Para hoy" mostraba las entregas de mañana y "Retrasadas" pintaba de
 * rojo las del día en curso. Las fechas que maneja el dashboard (`dueIso`,
 * `dayIso`) son días calendario que el operador elige a mano, no instantes.
 *
 * Los `todayIso` / `addDaysIso` / `nextDaysIsoSet` en UTC fueron ELIMINADOS a
 * propósito, no deprecados: mientras existieran, un import distraído
 * reintroducía el bug en silencio. El equivalente colombiano de cada uno está
 * más abajo.
 */

const DAY_MS = 86_400_000;

/**
 * Formatea un día calendario YYYY-MM-DD para el operador ("15 de julio de
 * 2026", es-CO). Puro respecto al reloj (no consulta `now`); si el input no
 * es un ISO date válido, lo devuelve tal cual (mejor crudo que crashear).
 */
export function formatIsoDateEs(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("es-CO", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

/* ── Día calendario en hora de Colombia ────────────────────────────────
 *
 * Los helpers de arriba usan día UTC (heredado de Orders). Para Chats eso es
 * incorrecto de forma visible: el corte de día a las 19:00 hora Colombia hacía
 * que un mensaje de las 20:00 del lunes se agrupara como martes, y que el
 * contador "Hoy" del inbox arrancara a las 7 de la tarde.
 *
 * Estas funciones son PURAS respecto al reloj salvo `todayBogotaIso()`, que es
 * la única que consulta `now` — se llama en render (regla 5 de la política de
 * estado), nunca dentro de un mapper ni de un `queryFn`.
 */

export const BOGOTA_TZ = "America/Bogota";

// "en-CA" produce YYYY-MM-DD, que es exactamente el formato ISO de día
// calendario que usa el resto del dashboard.
const BOGOTA_DAY_FMT = new Intl.DateTimeFormat("en-CA", {
  timeZone: BOGOTA_TZ,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const BOGOTA_TIME_FMT = new Intl.DateTimeFormat("es-CO", {
  timeZone: BOGOTA_TZ,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** ms epoch → día calendario YYYY-MM-DD en hora Colombia. `0`/NaN → "". */
export function bogotaDayIsoFromMs(ms: number): string {
  if (!Number.isFinite(ms) || ms === 0) return "";
  return BOGOTA_DAY_FMT.format(new Date(ms));
}

/** Unix epoch en SEGUNDOS (lo que emite el backend) → YYYY-MM-DD Colombia. */
export function bogotaDayIsoFromUnix(unixSeconds: number): string {
  if (!Number.isFinite(unixSeconds) || unixSeconds === 0) return "";
  return bogotaDayIsoFromMs(unixSeconds * 1000);
}

/** El día calendario de HOY en Colombia. Única función que mira el reloj. */
export function todayBogotaIso(): string {
  return BOGOTA_DAY_FMT.format(new Date());
}

/** Unix epoch (segundos) → "HH:MM" en hora Colombia, 24h. Independiente del
 *  timezone del navegador (el operador puede estar en otra máquina/VPN). */
export function formatBogotaHourMinute(unixSeconds: number): string {
  if (!Number.isFinite(unixSeconds) || unixSeconds === 0) return "";
  // es-CO con hour12:false emite "24:05" para la medianoche en algunos
  // runtimes de ICU; normalizamos a "00:05".
  return BOGOTA_TIME_FMT.format(new Date(unixSeconds * 1000)).replace(
    /^24:/,
    "00:",
  );
}

/** Día calendario a `days` días de hoy EN COLOMBIA (acepta negativos).
 *  Espejo colombiano del viejo `addDaysIso`. */
export function addDaysBogotaIso(days: number): string {
  return shiftIsoDay(todayBogotaIso(), days);
}

/** Los próximos `count` días calendario colombianos, incluyendo hoy.
 *  Espejo colombiano del viejo `nextDaysIsoSet` — lo usa la vista
 *  "Esta semana" de Órdenes. */
export function nextDaysBogotaIsoSet(count: number): Set<string> {
  const today = todayBogotaIso();
  const out = new Set<string>();
  for (let i = 0; i < count; i++) out.add(shiftIsoDay(today, i));
  return out;
}

/** Aritmética de día calendario sobre YYYY-MM-DD (sin tocar el reloj ni el TZ).
 *  Se hace en UTC a propósito: el input ya es un día, no un instante. */
export function shiftIsoDay(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

/** Días calendario entre dos ISO (`b - a`). */
export function diffIsoDays(a: string, b: string): number {
  const toUtc = (iso: string) => {
    const [y, m, d] = iso.split("-").map(Number);
    return Date.UTC(y, m - 1, d);
  };
  return Math.round((toUtc(b) - toUtc(a)) / DAY_MS);
}

/**
 * Etiqueta de un día calendario para el operador, estilo WhatsApp:
 * "Hoy" · "Ayer" · nombre del día dentro de la última semana · fecha larga.
 *
 * `today` se inyecta para que la función sea pura y testeable; el default
 * consulta el reloj (llamarla en render).
 */
export function formatDayLabelEs(iso: string, today: string = todayBogotaIso()): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso;
  const delta = diffIsoDays(iso, today);
  if (delta === 0) return "Hoy";
  if (delta === 1) return "Ayer";
  if (delta > 1 && delta < 7) {
    const [y, m, d] = iso.split("-").map(Number);
    const weekday = new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("es-CO", {
      weekday: "long",
      timeZone: "UTC",
    });
    return weekday.charAt(0).toUpperCase() + weekday.slice(1);
  }
  return formatIsoDateEs(iso);
}
