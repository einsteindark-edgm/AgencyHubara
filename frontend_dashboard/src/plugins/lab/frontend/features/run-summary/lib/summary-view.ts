/**
 * Textos del Resumen del laboratorio (plan §5.6; revisión 2026-09-29: en
 * palabras del operador). Diferencias pareadas con intervalo de 95 %: si el
 * intervalo cruza el cero, o hay pocas conversaciones en común, «todavía no
 * se puede saber» y el tablero no declara ganador. Por check, además, la caja
 * corrige por comparaciones múltiples (Holm); esos números quedan en el
 * detalle estadístico, plegado.
 */
import { armLabel, type ArmSummary, type CheckLevel, type Interval } from "@plugins/lab/frontend/entities/lab-run";

const MINUS = "−";
/** Mínimo de conversaciones en común para concluir (`MIN_CONCLUSIVE_SESSIONS` en `run/compare.py`). */
export const MIN_CONCLUSIVE_SESSIONS = 15;

function number(value: number): string {
  const rounded = Math.round(Math.abs(value) * 10) / 10;
  return rounded.toLocaleString("es-CO", { maximumFractionDigits: 1 });
}

function signed(rate: number): string {
  const v = rate * 100;
  if (Math.round(Math.abs(v) * 10) === 0) return "0";
  return `${v > 0 ? "+" : MINUS}${number(v)}`;
}

/** Proporción → "94 %". */
export function pct(rate: number | null): string {
  return rate === null ? "—" : `${Math.round(rate * 100)} %`;
}

/** Diferencia de proporciones → "+10 pp" / "−5,5 pp". */
export function points(rate: number): string {
  return `${signed(rate)} pp`;
}

export function intervalText(i: Interval): string {
  if (i.low === null || i.high === null || i.sessions === 0) return "sin conversaciones en común";
  return `IC 95 %: ${signed(i.low)} a ${points(i.high)} · ${i.sessions} conversaciones`;
}

export type ConclusionTone = "ok" | "bad" | "neutral";

/** El bot dentro de una frase: «con el bot nuevo con Jev», «con producción». */
function withArm(arm: string): string {
  if (arm === "A0") return "con producción";
  const label = armLabel(arm);
  return `con el ${label.charAt(0).toLowerCase()}${label.slice(1)}`;
}

export function conclusion(base: string, cand: string, i: Interval): { tone: ConclusionTone; text: string } {
  if (!i.conclusive || i.delta === null || i.delta === 0) {
    if (i.sessions === 0) return { tone: "neutral", text: "Todavía no hay conversaciones en común para comparar." };
    if (i.sessions < MIN_CONCLUSIVE_SESSIONS) {
      return {
        tone: "neutral",
        text: `Todavía no se puede saber: ${i.sessions} conversaciones en común son pocas (hacen falta al menos ${MIN_CONCLUSIVE_SESSIONS}).`,
      };
    }
    return { tone: "neutral", text: "Todavía no se puede saber: con estas conversaciones la diferencia puede ser casualidad." };
  }
  const more = i.delta > 0;
  const lead = withArm(cand);
  return {
    tone: more ? "ok" : "bad",
    text: `${lead.charAt(0).toUpperCase()}${lead.slice(1)} pasan ${number(i.delta * 100)} de cada 100 conversaciones ${more ? "más" : "menos"} que ${withArm(base)}.`,
  };
}

/** Los checks que más se movieron: primero los concluyentes, después por tamaño. */
export function topChecks<T extends Interval>(rows: readonly T[], n: number): T[] {
  return rows
    .filter((r) => r.sessions > 0 && r.delta !== null)
    .sort((a, b) => Number(b.conclusive) - Number(a.conclusive) || Math.abs(b.delta ?? 0) - Math.abs(a.delta ?? 0))
    .slice(0, n);
}

// ── En qué se diferencian los bots (cada check, con cada bot) ────────────────

export interface CheckCell {
  failed: number;
  applicable: number;
}

export interface CheckDifference {
  checkId: string;
  name: string;
  level: CheckLevel;
  /** Una celda por bot, en el orden pedido; `null` si ese bot no tiene datos del check. */
  cells: Array<CheckCell | null>;
}

const LEVEL_RANK: Record<CheckLevel, number> = { critico: 0, mayor: 1, menor: 2 };

/**
 * Los checks que fallaron al menos una vez con algún bot, con cuántas
 * conversaciones fallaron de las que les aplicaban (la tendencia de una
 * corrida es una sola ventana: se suman sus semanas). Primero los críticos,
 * después los que más fallaron.
 */
export function checkDifferences(summaries: Record<string, ArmSummary | undefined>, arms: string[]): CheckDifference[] {
  const rows = new Map<string, { name: string; level: CheckLevel; cells: Map<string, CheckCell> }>();
  for (const arm of arms) {
    for (const t of summaries[arm]?.trend ?? []) {
      const applicable = t.weeks.reduce((sum, w) => sum + w.applicable, 0);
      const passed = t.weeks.reduce((sum, w) => sum + w.passed, 0);
      const row = rows.get(t.check_id) ?? { name: t.name || t.check_id, level: t.level, cells: new Map<string, CheckCell>() };
      row.cells.set(arm, { failed: Math.max(applicable - passed, 0), applicable });
      rows.set(t.check_id, row);
    }
  }
  return [...rows.entries()]
    .map(([checkId, row]) => ({ checkId, name: row.name, level: row.level, cells: arms.map((a) => row.cells.get(a) ?? null) }))
    .filter((row) => row.cells.some((c) => c !== null && c.failed > 0))
    .sort(
      (a, b) =>
        LEVEL_RANK[a.level] - LEVEL_RANK[b.level] ||
        Math.max(...b.cells.map((c) => c?.failed ?? 0)) - Math.max(...a.cells.map((c) => c?.failed ?? 0)) ||
        a.name.localeCompare(b.name, "es"),
    );
}
