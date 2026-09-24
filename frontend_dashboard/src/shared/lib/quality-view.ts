/**
 * Vocabulario y tipos de vista de las gráficas de calidad de un bot
 * (`@/shared/ui` → `FailurePareto`, `StageFunnel`, `CheckTrend`,
 * `VerdictTiles`, `TrajectoryStrip`, `ComplianceMatrixTable`).
 *
 * Genérico a propósito: lo consumen varios plugins (Calidad LLM de
 * `agents_admin` y el laboratorio de conversaciones) que no pueden importarse
 * entre sí. Aquí solo viven formas de vista y su presentación (etiquetas,
 * glifos, tokens de color); los contratos, el orden del guion y las
 * derivaciones de dominio siguen en la entity de cada plugin, que mapea a
 * estas formas antes de pintar.
 */

// ── Vocabulario ───────────────────────────────────────────────────────────

/** Nivel de un check: una falla crítica reprueba el episodio sola. */
export type QualityLevel = "critico" | "mayor" | "menor";

/** Veredicto de un episodio (nunca un promedio). */
export type QualityVerdict = "FALLA" | "ALERTA" | "PASA" | "SIN_DATOS";

/** Resultado de UN check sobre un episodio. */
/**
 * `sin_senal` (modo turno del laboratorio): el check depende de turnos
 * posteriores (cierre, pedido registrado) y el turno solo no alcanza para
 * decidir. No es "desconocido" (el juez no supo) ni "sin evaluar".
 */
export type QualityCheckVerdict = "pasa" | "falla" | "no_aplica" | "desconocido" | "sin_senal";

/** Estado visual de un check: la falla toma su nivel; sin resultado = no evaluado. */
export type QualityStatus =
  | "pasa"
  | "critico"
  | "mayor"
  | "menor"
  | "no_aplica"
  | "desconocido"
  | "sin_senal"
  | "sin_resultado";

/** Conteo de episodios por veredicto. */
export type VerdictCounts = Record<QualityVerdict, number>;

export const QUALITY_VERDICT_ORDER: readonly QualityVerdict[] = ["FALLA", "ALERTA", "PASA", "SIN_DATOS"];

export const QUALITY_LEVEL_ORDER: readonly QualityLevel[] = ["critico", "mayor", "menor"];

const VERDICT_LABELS: Record<QualityVerdict, string> = {
  FALLA: "Falla",
  ALERTA: "Alerta",
  PASA: "Pasa",
  SIN_DATOS: "Sin datos",
};

const VERDICT_COLORS: Record<QualityVerdict, string> = {
  FALLA: "var(--color-red)",
  ALERTA: "var(--color-orange)",
  PASA: "var(--color-green)",
  SIN_DATOS: "var(--color-neutral)",
};

export function qualityVerdictLabel(v: QualityVerdict): string {
  return VERDICT_LABELS[v];
}

export function qualityVerdictColor(v: QualityVerdict): string {
  return VERDICT_COLORS[v];
}

const LEVEL_LABELS: Record<QualityLevel, string> = {
  critico: "crítico",
  mayor: "mayor",
  menor: "menor",
};

export function qualityLevelLabel(level: QualityLevel): string {
  return LEVEL_LABELS[level];
}

/** Estado visual de un check: la falla toma su nivel; sin veredicto = `sin_resultado`. */
export function qualityStatus(
  verdict: QualityCheckVerdict | null | undefined,
  level: QualityLevel,
): QualityStatus {
  if (!verdict) return "sin_resultado";
  return verdict === "falla" ? level : verdict;
}

const STATUS_GLYPHS: Record<QualityStatus, string> = {
  pasa: "✓",
  critico: "✗",
  mayor: "✗",
  menor: "✗",
  no_aplica: "–",
  desconocido: "?",
  sin_senal: "∅",
  sin_resultado: "·",
};

const STATUS_LABELS: Record<QualityStatus, string> = {
  pasa: "pasa",
  critico: "falla crítica",
  mayor: "falla mayor",
  menor: "falla menor",
  no_aplica: "no aplica",
  desconocido: "desconocido",
  sin_senal: "sin señal",
  sin_resultado: "sin evaluar",
};

const STATUS_COLORS: Record<QualityStatus, string> = {
  pasa: "var(--color-green)",
  critico: "var(--color-red)",
  mayor: "var(--color-orange)",
  menor: "var(--color-yellow)",
  no_aplica: "var(--color-neutral)",
  desconocido: "var(--color-violet)",
  sin_senal: "var(--color-fg-muted)",
  sin_resultado: "var(--color-line-strong)",
};

export function qualityStatusGlyph(s: QualityStatus): string {
  return STATUS_GLYPHS[s];
}

export function qualityStatusLabel(s: QualityStatus): string {
  return STATUS_LABELS[s];
}

export function qualityStatusColor(s: QualityStatus): string {
  return STATUS_COLORS[s];
}

/** Color de un nivel (Pareto, tendencia): mismo token que su falla. */
export function qualityLevelColor(level: QualityLevel): string {
  return STATUS_COLORS[level];
}

// ── Formatos ──────────────────────────────────────────────────────────────

/** `19 520 000` → `5 h 25 min`. */
export function formatDuration(ms: number): string {
  const totalSec = Math.round(ms / 1000);
  if (totalSec < 60) return `${totalSec} s`;
  const totalMin = Math.floor(totalSec / 60);
  if (totalMin < 60) return `${totalMin} min`;
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

/** Recorta a `max` caracteres CONTANDO la elipsis (a diferencia de `truncate`). */
export function clipText(text: string, max: number): string {
  return text.length > max ? text.slice(0, Math.max(0, max - 1)) + "…" : text;
}

// ── Pareto de fallos ──────────────────────────────────────────────────────

export interface ParetoItemView {
  check_id: string;
  name: string;
  level: QualityLevel;
  failures: number;
}

export type ParetoRowView<T extends ParetoItemView = ParetoItemView> = T & {
  /** Fallos acumulados hasta esta barra (inclusive). */
  cumulative: number;
  /** cumulative / total — 0..1. */
  share: number;
};

/** Pareto: barras por fallos (desc) con acumulado y participación 0..1. */
export function paretoWithCumulative<T extends ParetoItemView>(items: readonly T[]): ParetoRowView<T>[] {
  const sorted = [...items]
    .filter((i) => i.failures > 0)
    .sort((a, b) => b.failures - a.failures || a.check_id.localeCompare(b.check_id));
  const total = sorted.reduce((s, i) => s + i.failures, 0);
  let acc = 0;
  return sorted.map((i) => {
    acc += i.failures;
    return { ...i, cumulative: acc, share: total ? acc / total : 0 };
  });
}

// ── Tendencia semanal por check ───────────────────────────────────────────

export interface TrendWeekView {
  /** Lunes de la semana, YYYY-MM-DD. */
  week: string;
  applicable: number;
  passed: number;
  /** passed / applicable; null = semana sin episodios aplicables. */
  rate: number | null;
}

export interface TrendSeriesView {
  check_id: string;
  name: string;
  level: QualityLevel;
  weeks: readonly TrendWeekView[];
}

export interface WeeklyDelta {
  last: number | null;
  previous: number | null;
  delta: number | null;
}

/**
 * Última tasa semanal con dato vs. la anterior con dato (las semanas sin
 * episodios aplicables — `rate: null` — no cuentan como "semana anterior").
 */
export function weeklyDelta(weeks: readonly TrendWeekView[]): WeeklyDelta {
  const rated = weeks.filter((w) => w.rate !== null);
  const last = rated.at(-1)?.rate ?? null;
  const previous = rated.length >= 2 ? (rated.at(-2)?.rate ?? null) : null;
  return {
    last,
    previous,
    delta: last !== null && previous !== null ? last - previous : null,
  };
}

/** ¿El check falló al menos una vez en la ventana? */
export function trendHasFailures(trend: Pick<TrendSeriesView, "weeks">): boolean {
  return trend.weeks.some((w) => w.passed < w.applicable);
}

// ── Etapas del guion de ventas (rótulo, color y orden) ─────────────────────
// Vocabulario de vista compartido por Calidad LLM y el laboratorio.

export const QUALITY_STAGE_ORDER = [
  "descubrimiento",
  "variantes",
  "confirmacion",
  "datos_envio",
  "cierre",
  "postcierre",
  "transversal",
] as const;

const STAGE_LABELS: Record<string, string> = {
  descubrimiento: "descubrimiento",
  variantes: "variantes",
  confirmacion: "confirmación",
  datos_envio: "datos de envío",
  cierre: "cierre",
  postcierre: "post-cierre",
  transversal: "transversal",
};

/** Cada etapa con su token (identidad reforzada SIEMPRE con texto visible). */
const STAGE_COLORS: Record<string, string> = {
  descubrimiento: "var(--color-info)",
  variantes: "var(--color-violet)",
  confirmacion: "var(--color-cyan)",
  datos_envio: "var(--color-yellow)",
  cierre: "var(--color-pink)",
  postcierre: "var(--color-accent)",
  transversal: "var(--color-neutral)",
};

export function qualityStageLabel(stage: string | null | undefined): string {
  if (!stage) return "sin etapa";
  return STAGE_LABELS[stage] ?? stage.replaceAll("_", " ");
}

export function qualityStageColor(stage: string | null | undefined): string {
  return (stage && STAGE_COLORS[stage]) || "var(--color-neutral)";
}

/** Posición en el guion; las etapas desconocidas van al final. */
export function qualityStageRank(stage: string | null | undefined): number {
  const i = QUALITY_STAGE_ORDER.indexOf((stage ?? "") as (typeof QUALITY_STAGE_ORDER)[number]);
  return i === -1 ? QUALITY_STAGE_ORDER.length : i;
}

// ── Embudo de etapa terminal ──────────────────────────────────────────────

/** Una fila del embudo YA ordenada y rotulada por el dueño del dominio. */
export interface FunnelRowView extends VerdictCounts {
  stage: string;
  /** Rótulo visible de la etapa. */
  label: string;
  /** Token de color de la etapa (`var(--color-*)`). */
  color: string;
}

export function verdictCountsTotal(row: VerdictCounts): number {
  return row.FALLA + row.ALERTA + row.PASA + row.SIN_DATOS;
}

// ── Matriz de cumplimiento (episodios × checks) ───────────────────────────

export interface MatrixColumnView {
  id: string;
  name: string;
  level: QualityLevel;
}

export interface MatrixGroupView {
  /** Clave estable del grupo (p. ej. la etapa). */
  key: string;
  label: string;
  /** Token de color del encabezado del grupo. */
  color: string;
  columns: readonly MatrixColumnView[];
}

export interface MatrixRowView {
  /** Clave estable de la fila (única en la tabla). */
  key: string;
  verdict: QualityVerdict;
  /** Rótulo corto (mono) del episodio. */
  label: string;
  /** Tooltip de la primera columna (id completo). */
  title: string;
  /** Texto secundario tras el rótulo (fecha, etiqueta de cierre…). */
  meta: string;
  /** Veredicto por id de check; ausente = sin evaluar. */
  checks: Readonly<Record<string, QualityCheckVerdict>>;
}

/** Estado de una celda: veredicto de la fila para esa columna + nivel del check. */
export function matrixCellStatus(row: MatrixRowView, column: MatrixColumnView): QualityStatus {
  return qualityStatus(row.checks[column.id], column.level);
}


/** Filas del embudo (`stats.funnel`) → vista de `StageFunnel`: en el orden
 * del guion y con rótulo y color de cada etapa. */
export function toQualityFunnel(rows: readonly (VerdictCounts & { stage: string })[]): FunnelRowView[] {
  return [...rows]
    .sort((a, b) => qualityStageRank(a.stage) - qualityStageRank(b.stage))
    .map((r) => ({ ...r, label: qualityStageLabel(r.stage), color: qualityStageColor(r.stage) }));
}
