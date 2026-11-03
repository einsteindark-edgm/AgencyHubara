import type { ReviewTone } from "../model";

/**
 * Clases Tailwind por tono — tokens del theme (`--color-ok/warn/danger/info`
 * + `-soft`), nunca colores literales: el dashboard es oscuro y un color
 * inventado queda ilegible.
 */
export const TONE_CLASS: Record<ReviewTone, string> = {
  neutral: "bg-neutral-soft text-fg-soft",
  info: "bg-info-soft text-info",
  warn: "bg-warn-soft text-warn",
  ok: "bg-ok-soft text-ok",
  danger: "bg-danger-soft text-danger",
};
