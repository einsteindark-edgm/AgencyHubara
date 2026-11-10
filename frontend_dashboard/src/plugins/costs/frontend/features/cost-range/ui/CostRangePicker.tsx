/**
 * Selector del rango de la pestaña Costos: atajos (7 días, 2 semanas, 30 días,
 * este mes, mes pasado) y las dos fechas a mano. Solo propaga rangos válidos;
 * uno inválido se explica debajo y no dispara ninguna consulta.
 */

import { useId, useState } from "react";

import type { CostRange } from "@plugins/costs/frontend/entities/cost-report";

import { RANGE_PRESETS, presetRange, sameRange, validateRange } from "../model/range";

interface Props {
  value: CostRange;
  onChange: (range: CostRange) => void;
  today: string;
  maxDays: number;
}

const INPUT =
  "rounded-md border border-line-strong bg-canvas px-2 py-1 text-[12.5px] text-fg " +
  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent";

export function CostRangePicker({ value, onChange, today, maxDays }: Props) {
  const startId = useId();
  const endId = useId();
  const [draft, setDraft] = useState<CostRange | null>(null);
  const shown = draft ?? value;
  const problem = draft ? validateRange(draft, today, maxDays) : null;

  const propose = (next: CostRange) => {
    if (validateRange(next, today, maxDays) === null) {
      setDraft(null);
      onChange(next);
    } else {
      setDraft(next);
    }
  };

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <div role="group" aria-label="Atajos de fecha" className="flex flex-wrap gap-1">
          {RANGE_PRESETS.map((p) => {
            const range = presetRange(p.id, today);
            const active = !draft && sameRange(range, value);
            return (
              <button
                key={p.id}
                type="button"
                aria-pressed={active}
                onClick={() => propose(range)}
                className={
                  "rounded-md border px-2.5 py-1 text-[12px] font-semibold focus-visible:outline-2 " +
                  "focus-visible:outline-offset-2 focus-visible:outline-accent " +
                  (active
                    ? "border-accent bg-accent-soft text-accent"
                    : "border-line-strong text-fg-soft hover:bg-line")
                }
              >
                {p.label}
              </button>
            );
          })}
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-[12px] text-fg-muted">
          <label htmlFor={startId}>Desde</label>
          <input
            id={startId}
            type="date"
            className={INPUT}
            value={shown.start}
            max={today}
            onChange={(e) => propose({ start: e.target.value, end: shown.end })}
          />
          <label htmlFor={endId}>Hasta</label>
          <input
            id={endId}
            type="date"
            className={INPUT}
            value={shown.end}
            max={today}
            onChange={(e) => propose({ start: shown.start, end: e.target.value })}
          />
        </div>
      </div>
      {problem ? (
        <p role="alert" className="m-0 text-[12px] text-warn">
          {problem}
        </p>
      ) : null}
    </div>
  );
}
