/**
 * Registro de cambios del cupón (D6): quién (usuario verificado de la
 * sesión), qué y cuándo. El backend lo entrega más nuevo primero.
 */

import type { CouponChange } from "@plugins/marketing/frontend/entities/coupon";
import { fmtDateTimeMs } from "@plugins/marketing/frontend/lib/format";

import { changeActionLabel, changeSummary } from "../model/changes";

export function ChangesLog({
  changes,
  productTitles,
}: {
  changes: CouponChange[];
  /** id → título del catálogo (los productos se leen por su nombre). */
  productTitles: ReadonlyMap<string, string>;
}) {
  if (changes.length === 0) {
    return <p className="text-[11.5px] text-fg-faint">Todavía no hay cambios registrados.</p>;
  }
  return (
    <ul className="flex flex-col divide-y divide-line">
      {changes.map((c, i) => {
        const ms = Date.parse(c.ts);
        const lines = changeSummary(c, productTitles);
        return (
          <li key={`${c.ts}-${i}`} className="flex flex-col gap-0.5 py-2">
            <div className="flex items-center gap-2 text-[12px]">
              <span className="font-semibold text-fg">{c.actor || "—"}</span>
              <span className="text-fg-soft">{changeActionLabel(c.action)}</span>
              <span className="ml-auto text-[10.5px] tabular-nums text-fg-faint">
                {Number.isNaN(ms) ? c.ts : fmtDateTimeMs(ms)}
              </span>
            </div>
            {lines.map((line) => (
              <span key={line} className="text-[11px] leading-snug text-fg-muted">
                {line}
              </span>
            ))}
          </li>
        );
      })}
    </ul>
  );
}
