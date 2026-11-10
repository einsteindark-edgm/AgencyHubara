/**
 * Rangos de fecha de la pestaña Costos (días calendario de Bogotá, YYYY-MM-DD).
 * Puro: `today` llega de afuera (el backend lo manda con el catálogo), así las
 * mismas reglas valen acá y en el servidor.
 */

import { diffIsoDays, shiftIsoDay } from "@/shared/lib";

import type { CostRange } from "@plugins/costs/frontend/entities/cost-report";

export type RangePreset = "7d" | "14d" | "30d" | "month" | "prev-month";

export const RANGE_PRESETS: Array<{ id: RangePreset; label: string }> = [
  { id: "7d", label: "7 días" },
  { id: "14d", label: "2 semanas" },
  { id: "30d", label: "30 días" },
  { id: "month", label: "Este mes" },
  { id: "prev-month", label: "Mes pasado" },
];

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

export function presetRange(preset: RangePreset, today: string): CostRange {
  const lastDays = (n: number) => ({ start: shiftIsoDay(today, -(n - 1)), end: today });
  switch (preset) {
    case "7d":
      return lastDays(7);
    case "14d":
      return lastDays(14);
    case "30d":
      return lastDays(30);
    case "month":
      return { start: `${today.slice(0, 8)}01`, end: today };
    case "prev-month": {
      const end = shiftIsoDay(`${today.slice(0, 8)}01`, -1);
      return { start: `${end.slice(0, 8)}01`, end };
    }
  }
}

/** Mismo criterio que el backend (`DateRange.parse`); null = válido. */
export function validateRange(range: CostRange, today: string, maxDays: number): string | null {
  if (!ISO_DAY.test(range.start) || !ISO_DAY.test(range.end)) {
    return "Elige una fecha de inicio y una fecha final.";
  }
  if (range.start > range.end) return "La fecha de inicio es posterior a la fecha final.";
  if (range.end > today) return "La fecha final no puede ser futura.";
  const days = diffIsoDays(range.start, range.end) + 1;
  if (days > maxDays) return `El rango no puede pasar de ${maxDays} días (elegiste ${days}).`;
  return null;
}

export function sameRange(a: CostRange, b: CostRange): boolean {
  return a.start === b.start && a.end === b.end;
}
