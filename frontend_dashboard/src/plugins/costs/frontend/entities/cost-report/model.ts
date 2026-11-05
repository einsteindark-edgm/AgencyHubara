/**
 * Dominio de la pestaña Costos (camelCase) y lo que se calcula en el cliente:
 * formato de dinero y el total productivo de las tarjetas que ya llegaron.
 *
 * Dinero en micros enteros (1 unidad = 1_000_000), igual que el backend.
 */

export type CostStatus = "ok" | "not_connected" | "error";
export type CostSource = "billed" | "metered" | "computed" | "conversations";

export interface CostProvider {
  id: string;
  label: string;
  connected: boolean;
}

export interface CostCatalog {
  providers: CostProvider[];
  today: string;
  defaultStart: string;
  defaultEnd: string;
  maxDays: number;
}

export interface CostRange {
  start: string;
  end: string;
}

export interface CostLine {
  key: string;
  label: string;
  group: string | null;
  currency: string;
  amountMicros: number;
  usdMicros: number | null;
  copMicros: number | null;
  production: boolean;
  quantity: number | null;
  unit: string | null;
}

export interface ProviderReport {
  provider: string;
  status: CostStatus;
  source: CostSource | null;
  start: string;
  end: string;
  productionUsdMicros: number | null;
  productionCopMicros: number | null;
  otherUsdMicros: number | null;
  daily: Array<{ date: string; usdMicros: number }>;
  lines: CostLine[];
  notes: string[];
  warnings: string[];
  error: string | null;
  howToConnect: string | null;
  fxNote: string | null;
  fetchedAt: string | null;
  cached: boolean;
}

/** De dónde sale la cifra, en palabras del operador. */
export const SOURCE_LABEL: Record<CostSource, string> = {
  billed: "Factura del proveedor",
  metered: "Medición del proveedor",
  computed: "Uso del proveedor × su tarifa",
  conversations: "Registrado en cada conversación",
};

const MICROS = 1_000_000;

function decimals(value: number, digits: number): string {
  return value.toLocaleString("es-CO", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** Dólares: centavos; por debajo de US$1 (y distinto de 0), cuatro decimales. */
export function fmtUsd(micros: number): string {
  const usd = micros / MICROS;
  const digits = usd !== 0 && Math.abs(usd) < 1 ? 4 : 2;
  return `US$${decimals(usd, digits)}`;
}

/** Pesos sin decimales. */
export function fmtCop(micros: number): string {
  return `$${decimals(Math.round(micros / MICROS), 0)} COP`;
}

/** Una cifra en su propia moneda (las líneas vienen en la del proveedor). */
export function fmtMoney(micros: number, currency: string): string {
  const cur = currency.toUpperCase();
  if (cur === "USD") return fmtUsd(micros);
  if (cur === "COP") return fmtCop(micros);
  const symbol = cur === "CNY" ? "¥" : "";
  return `${symbol}${decimals(micros / MICROS, 2)} ${cur}`;
}

/** Estado de una tarjeta para el total: lo que respondió, lo que falta. */
export interface TotalEntry {
  id: string;
  label: string;
  report: ProviderReport | undefined;
  state: "loading" | "done" | "failed";
}

export interface CostTotal {
  usdMicros: number;
  copMicros: number;
  complete: boolean;
  parts: Array<{ id: string; label: string; usdMicros: number }>;
  missing: Array<{ id: string; label: string; reason: string }>;
}

function missingReason(entry: TotalEntry): string | null {
  if (entry.state === "loading") return "consultando";
  if (entry.state === "failed" || !entry.report) return "falló la consulta";
  if (entry.report.status === "not_connected") return "sin conectar";
  if (entry.report.status === "error") return "el proveedor falló";
  if (entry.report.productionUsdMicros === null) return "sin tasa de cambio";
  return null;
}

/** Total productivo: suma de lo que respondió; lo demás se nombra, nunca vale $0. */
export function summarizeTotal(entries: TotalEntry[]): CostTotal {
  const parts: CostTotal["parts"] = [];
  const missing: CostTotal["missing"] = [];
  let copMicros = 0;
  for (const entry of entries) {
    const reason = missingReason(entry);
    if (reason !== null || !entry.report) {
      missing.push({ id: entry.id, label: entry.label, reason: reason ?? "falló la consulta" });
      continue;
    }
    parts.push({ id: entry.id, label: entry.label, usdMicros: entry.report.productionUsdMicros ?? 0 });
    copMicros += entry.report.productionCopMicros ?? 0;
  }
  return {
    usdMicros: parts.reduce((sum, p) => sum + p.usdMicros, 0),
    copMicros,
    complete: missing.length === 0,
    parts,
    missing,
  };
}

/** Color de cada proveedor en la barra de participación (tokens del tema). */
export const PROVIDER_COLOR: Record<string, string> = {
  deepseek: "bg-info",
  openrouter: "bg-purple",
  gemini: "bg-teal",
  whatsapp: "bg-green",
  temporal: "bg-orange",
};

/** Texto para el operador cuando la llamada a NUESTRO API falló. */
export function costErrorDetail(err: unknown): string {
  if (err && typeof err === "object" && "status" in err && "body" in err) {
    const body = (err as { body: unknown }).body;
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail?: unknown }).detail;
      if (typeof detail === "string" && detail.trim()) return detail;
    }
    return `Algo falló en el servidor (error ${(err as { status: number }).status}).`;
  }
  if (err instanceof TypeError) return "No hubo conexión con el servidor.";
  return "El servidor respondió algo que no se pudo leer.";
}

/** Días calendario del rango, contando ambos extremos. */
export function rangeDays(range: CostRange): number {
  const toUtc = (iso: string) => {
    const [y, m, d] = iso.split("-").map(Number);
    return Date.UTC(y, m - 1, d);
  };
  return Math.round((toUtc(range.end) - toUtc(range.start)) / 86_400_000) + 1;
}

/** «22 oct – 4 nov 2026 · 14 días». */
export function describeCostRange(range: CostRange): string {
  const fmt = (iso: string, withYear: boolean) => {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("es-CO", {
      day: "numeric",
      month: "short",
      ...(withYear ? { year: "numeric" } : {}),
      timeZone: "UTC",
    });
  };
  const days = rangeDays(range);
  const sameYear = range.start.slice(0, 4) === range.end.slice(0, 4);
  const span = range.start === range.end ? fmt(range.end, true) : `${fmt(range.start, !sameYear)} – ${fmt(range.end, true)}`;
  return `${span} · ${days === 1 ? "1 día" : `${days} días`}`;
}

/** Estado de cada query de tarjeta → entrada del total (mismo orden que el catálogo). */
export function totalEntries(
  providers: CostProvider[],
  queries: Array<{ data: ProviderReport | undefined; isPending: boolean; isError: boolean }>,
): TotalEntry[] {
  return providers.map((p, i) => {
    const q = queries[i];
    const state = !q || q.isPending ? "loading" : q.isError ? "failed" : "done";
    return { id: p.id, label: p.label, report: q?.data, state };
  });
}
