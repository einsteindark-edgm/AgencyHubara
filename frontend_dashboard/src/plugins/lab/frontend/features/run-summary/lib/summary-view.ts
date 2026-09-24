/**
 * Textos del Resumen del laboratorio (plan §5.6): diferencias pareadas con
 * intervalo de 95 %. Si el intervalo cruza el cero, o hay pocas
 * conversaciones en común, la diferencia es "aún no concluyente" y el tablero
 * no declara ganador. Por check, además, la caja corrige por comparaciones
 * múltiples (Holm).
 */
import { armLabel, type Interval } from "@plugins/lab/frontend/entities/lab-run";

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

export function conclusion(base: string, cand: string, i: Interval): { tone: ConclusionTone; text: string } {
  if (!i.conclusive || i.delta === null || i.delta === 0) {
    if (i.sessions > 0 && i.sessions < MIN_CONCLUSIVE_SESSIONS) {
      return {
        tone: "neutral",
        text: `Aún no concluyente: ${i.sessions} conversaciones en común son pocas (hacen falta ${MIN_CONCLUSIVE_SESSIONS})`,
      };
    }
    return { tone: "neutral", text: "Aún no concluyente: el intervalo cruza el cero" };
  }
  const more = i.delta > 0;
  return {
    tone: more ? "ok" : "bad",
    text: `${armLabel(cand)} pasa ${number(i.delta * 100)} pp ${more ? "más" : "menos"} episodios que ${armLabel(base)}`,
  };
}

/** Los checks que más se movieron: primero los concluyentes, después por tamaño. */
export function topChecks<T extends Interval>(rows: readonly T[], n: number): T[] {
  return rows
    .filter((r) => r.sessions > 0 && r.delta !== null)
    .sort((a, b) => Number(b.conclusive) - Number(a.conclusive) || Math.abs(b.delta ?? 0) - Math.abs(a.delta ?? 0))
    .slice(0, n);
}
