/**
 * Fichas de calidad (diseño del laboratorio §09, compartidas con Calidad LLM):
 * el veredicto de un episodio o un turno, el nivel de un check y las fichas de
 * asuntos y de quién decidió. Solo tokens del tema.
 */

import type { ReactNode } from "react";

import { LEVEL_HELP, levelLabel, type QualityLevel, type QualityVerdict } from "@/shared/lib";

export type ChipTone = "ok" | "bad" | "warn" | "classifier" | "neutral";

const CHIP_TONE: Record<ChipTone, string> = {
  ok: "border-transparent bg-ok-soft text-ok",
  bad: "border-transparent bg-danger-soft text-danger",
  warn: "border-transparent bg-warn-soft text-warn",
  classifier: "border-transparent bg-violet-soft text-violet",
  neutral: "border-line-strong bg-white/[0.03] text-fg-soft",
};

export function Chip({ tone, children }: { tone: ChipTone; children: ReactNode }) {
  return (
    <span className={"inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-[5px] text-[11.5px] font-medium leading-none " + CHIP_TONE[tone]}>
      {children}
    </span>
  );
}

const VERDICT_TONE: Record<QualityVerdict, string> = {
  PASA: "bg-ok-soft text-ok",
  ALERTA: "bg-warn-soft text-warn",
  FALLA: "bg-danger-soft text-danger",
  SIN_DATOS: "bg-neutral-soft text-fg-muted",
};

export function VerdictBadge({ verdict, prefix }: { verdict: QualityVerdict; prefix?: string }) {
  const text = verdict === "SIN_DATOS" ? "SIN DATOS" : verdict;
  return (
    <span className={"whitespace-nowrap rounded-full px-1.5 py-[3px] text-[9.5px] font-semibold leading-none tracking-[0.02em] " + VERDICT_TONE[verdict]}>
      {prefix ? `${prefix} ${text}` : text}
    </span>
  );
}

const LEVEL_TONE: Record<QualityLevel, string> = {
  critico: "bg-danger-soft text-danger",
  mayor: "bg-warn-soft text-warn",
  menor: "bg-neutral-soft text-fg-muted",
};

/** El nivel de un check (crítico, mayor, menor); el título dice qué hace con el veredicto. */
export function LevelPill({ level }: { level: QualityLevel }) {
  return (
    <span title={LEVEL_HELP[level]} className={"whitespace-nowrap rounded-full px-1.5 py-[3px] text-[10px] font-semibold leading-none " + LEVEL_TONE[level]}>
      {levelLabel(level)}
    </span>
  );
}
