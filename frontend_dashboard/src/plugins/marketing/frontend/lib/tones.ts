/**
 * Clases Tailwind por "tono" semántico — tokens del theme dark macOS
 * (`--color-ok/warn/danger/info` + `-soft`), nunca hex hardcodeado.
 * Compartido por las pills de estado del sidebar y del builder.
 */

import type { StatusTone } from "@plugins/marketing/frontend/entities/campaign";

export const TONE_CLS: Record<StatusTone, string> = {
  neutral: "bg-line/60 text-fg-muted",
  info: "bg-info-soft text-info",
  warn: "bg-warn-soft text-warn",
  ok: "bg-ok-soft text-ok",
  danger: "bg-danger-soft text-danger",
};
