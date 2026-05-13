/**
 * Helpers de formato genéricos (sin dependencias de dominio).
 * Si un helper acaba siendo usado por una sola feature, debe migrarse de vuelta
 * al folder de esa feature (anti-pattern §11).
 */

const COP = new Intl.NumberFormat("es-CO");

/** Formato moneda. `short = true` produce `$ 2.1 M` / `$ 124 k`. */
export function fmtMoney(n: number, short = false): string {
  if (short) {
    if (n >= 1_000_000) return "$ " + (n / 1_000_000).toFixed(1) + " M";
    if (n >= 1_000) return "$ " + Math.round(n / 1_000) + " k";
  }
  return "$ " + COP.format(n);
}

/** Hoy/Mañana/Ayer + nombre corto de día para fechas ISO `YYYY-MM-DD`. */
export function dayChipShort(iso: string): string {
  const map: Record<string, string> = {
    "2026-05-11": "Ayer",
    "2026-05-12": "Hoy",
    "2026-05-13": "Mañana",
    "2026-05-14": "Jue 14",
    "2026-05-15": "Vie 15",
    "2026-05-16": "Sáb 16",
    "2026-05-17": "Dom 17",
    "2026-05-18": "Lun 18",
    "2026-05-19": "Mar 19",
  };
  return map[iso] ?? iso;
}

/** Variante larga con mes (`Hoy · 12 may`). */
export function dayChip(iso: string): string {
  const map: Record<string, string> = {
    "2026-05-11": "Ayer · 11 may",
    "2026-05-12": "Hoy · 12 may",
    "2026-05-13": "Mañana · 13 may",
    "2026-05-14": "Jue 14 may",
    "2026-05-15": "Vie 15 may",
    "2026-05-16": "Sáb 16 may",
    "2026-05-17": "Dom 17 may",
    "2026-05-18": "Lun 18 may",
    "2026-05-19": "Mar 19 may",
  };
  return map[iso] ?? iso;
}

/** Trunca a `n` caracteres añadiendo elipsis. */
export function truncate(s: string | null | undefined, n: number): string {
  if (!s) return "";
  return s.length > n ? s.slice(0, n).trimEnd() + "…" : s;
}

/** Cuenta palabras separadas por whitespace. */
export function wordCount(s: string): number {
  return s.trim().split(/\s+/).filter(Boolean).length;
}

/** Unix epoch (segundos) → "HH:MM" en locale del usuario. Promovido desde
 *  `features/session-list/model/format.ts` cuando el adaptador de chat
 *  pasó a necesitarlo también. */
export function formatHourMinute(unixSeconds: number): string {
  if (!unixSeconds) return "";
  const d = new Date(unixSeconds * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
