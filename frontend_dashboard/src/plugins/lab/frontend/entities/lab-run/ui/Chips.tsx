/**
 * Chips del laboratorio (diseño §09): el veredicto de un episodio (`vb`) y
 * las fichas de asuntos y checks del turno (`hchip`). Solo tokens del tema.
 */

import type { ReactNode } from "react";

import type { EpisodeVerdict } from "../model";

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

const VERDICT_TONE: Record<EpisodeVerdict, string> = {
  PASA: "bg-ok-soft text-ok",
  ALERTA: "bg-warn-soft text-warn",
  FALLA: "bg-danger-soft text-danger",
  SIN_DATOS: "bg-neutral-soft text-fg-muted",
};

export function VerdictBadge({ verdict, prefix }: { verdict: EpisodeVerdict; prefix?: string }) {
  const text = verdict === "SIN_DATOS" ? "SIN DATOS" : verdict;
  return (
    <span className={"whitespace-nowrap rounded-full px-1.5 py-[3px] text-[9.5px] font-semibold leading-none tracking-[0.02em] " + VERDICT_TONE[verdict]}>
      {prefix ? `${prefix} ${text}` : text}
    </span>
  );
}
