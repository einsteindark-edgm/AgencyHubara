/**
 * Aritmética pura del calendario del inbox — sin React, sin reloj, sin zonas.
 *
 * Todo opera sobre strings: un día es `YYYY-MM-DD` y un mes es `YYYY-MM`. Las
 * conversiones intermedias se hacen en UTC a propósito: el input ya es un día
 * calendario (Bogotá lo resolvió aguas arriba, en `entities/chat`), no un
 * instante. Meter `America/Bogota` acá sería aplicar el offset dos veces.
 */

import type { DateRange } from "./useInboxFilters";

export interface MonthCell {
  iso: string;
  /** false = día de relleno del mes anterior/siguiente (se pinta apagado). */
  inMonth: boolean;
}

function parseMonth(month: string): { y: number; m: number } {
  const [y, m] = month.split("-").map(Number);
  return { y, m };
}

function isoOf(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/** Mes (`YYYY-MM`) al que pertenece un día. */
export function monthOf(dayIso: string): string {
  return dayIso.slice(0, 7);
}

export function shiftMonth(month: string, delta: number): string {
  const { y, m } = parseMonth(month);
  const d = new Date(Date.UTC(y, m - 1 + delta, 1));
  return isoOf(d).slice(0, 7);
}

/** "Septiembre 2026" — encabezado de la grilla. Se arma con el mes suelto en
 *  vez de `{month, year}` porque es-CO emite "septiembre de 2026", demasiado
 *  largo para los 280px de la sidebar. */
export function monthLabelEs(month: string): string {
  const { y, m } = parseMonth(month);
  const name = new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("es-CO", {
    month: "long",
    timeZone: "UTC",
  });
  return `${name.charAt(0).toUpperCase()}${name.slice(1)} ${y}`;
}

/**
 * Grilla del mes en semanas de 7 días, **empezando en lunes** (convención
 * colombiana; `getUTCDay()` devuelve 0 para domingo, así que se rota).
 * Incluye los días de relleno del mes anterior y del siguiente para que todas
 * las filas tengan 7 celdas y la grilla no baile al cambiar de mes.
 */
export function buildMonthGrid(month: string): MonthCell[][] {
  const { y, m } = parseMonth(month);
  const first = new Date(Date.UTC(y, m - 1, 1));
  // Domingo(0) → 6; lunes(1) → 0; ... sábado(6) → 5.
  const leading = (first.getUTCDay() + 6) % 7;
  const daysInMonth = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const totalCells = Math.ceil((leading + daysInMonth) / 7) * 7;

  const weeks: MonthCell[][] = [];
  for (let i = 0; i < totalCells; i++) {
    const dayNumber = i - leading + 1;
    const date = new Date(Date.UTC(y, m - 1, dayNumber));
    const cell: MonthCell = {
      iso: isoOf(date),
      inMonth: dayNumber >= 1 && dayNumber <= daysInMonth,
    };
    if (i % 7 === 0) weeks.push([]);
    weeks[weeks.length - 1].push(cell);
  }
  return weeks;
}

/**
 * Qué rango produce clickear `day` sobre el rango actual.
 *
 * Ciclo de dos clicks: el primero ancla (`to: null` = rango abierto), el
 * segundo cierra. Si el segundo click es ANTERIOR al ancla se invierte el
 * rango en vez de descartarlo — el operador que arrastra hacia atrás quiere
 * ese rango, no empezar de cero.
 */
export function nextRange(current: DateRange, day: string): DateRange {
  const isOpen = current.from !== null && current.to === null;
  if (!isOpen) return { from: day, to: null };
  const anchor = current.from!;
  return day < anchor ? { from: day, to: anchor } : { from: anchor, to: day };
}

/** ¿`day` cae dentro del rango YA cerrado? (para pintar la banda intermedia) */
export function isInSelectedRange(day: string, range: DateRange): boolean {
  if (!range.from) return false;
  const to = range.to ?? range.from;
  return day >= range.from && day <= to;
}
