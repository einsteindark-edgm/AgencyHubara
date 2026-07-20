/**
 * Clases Tailwind por "tono" semántico — tokens del theme dark macOS
 * (`--color-ok/warn/danger/info` + `-soft`), nunca hex hardcodeado.
 * Compartido por las pills de estado del sidebar y del builder.
 */

import type { StatusTone } from "@plugins/marketing/frontend/entities/campaign";

/** `violet` (token `--color-violet` del theme) no es un StatusTone de
 *  campaña — lo usa el chip "Manual" de la curaduría de audiencia. */
export const TONE_CLS: Record<StatusTone | "violet", string> = {
  neutral: "bg-line/60 text-fg-muted",
  info: "bg-info-soft text-info",
  warn: "bg-warn-soft text-warn",
  ok: "bg-ok-soft text-ok",
  danger: "bg-danger-soft text-danger",
  violet: "bg-violet-soft text-violet",
};
