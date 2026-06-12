/**
 * Helpers puros de fecha-calendario (YYYY-MM-DD) para filtros y defaults de
 * formularios. Centralizan el patrón `new Date(...).toISOString().slice(0,10)`
 * que estaba copiado en componentes (OrdersFilters, OrdersHeader,
 * ReadyForShip, ConfirmPaymentAction) — los componentes no deben hacer
 * aritmética de reloj inline (auditoría 2026-06-10, F0.4).
 *
 * Semántica: día calendario **UTC** — idéntica al código que reemplazan.
 * TODO(F5): evaluar mover a America/Bogota; con UTC el corte de "hoy" ocurre
 * a las 19:00 hora Colombia. Cambiarlo altera contadores ("Para hoy") y
 * defaults de agendamiento, así que requiere decisión de producto, no es
 * un refactor silencioso.
 */

const DAY_MS = 86_400_000;

/** Día calendario de hoy en formato YYYY-MM-DD (UTC). */
export function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Día calendario a `days` días de hoy (acepta negativos), YYYY-MM-DD (UTC). */
export function addDaysIso(days: number): string {
  return new Date(Date.now() + days * DAY_MS).toISOString().slice(0, 10);
}

/** Set con los próximos `count` días calendario incluyendo hoy (UTC). */
export function nextDaysIsoSet(count: number): Set<string> {
  const out = new Set<string>();
  for (let i = 0; i < count; i++) out.add(addDaysIso(i));
  return out;
}
