/**
 * Total del sistema productivo en el rango: la suma de lo que cada proveedor
 * ya respondió (solo lo productivo), en dólares y pesos, con la participación
 * de cada uno. Si falta alguno (sin conectar, falló o todavía consultando) el
 * total dice «Parcial» y nombra qué falta — nunca lo cuenta como $0.
 *
 * El operador elige qué sumar con una casilla por proveedor (p. ej. solo
 * DeepSeek, Jev y Gemini para ver lo que cuestan los modelos). Un proveedor
 * desmarcado no suma ni cuenta para «Parcial». La elección se recuerda en este
 * navegador (`model/selection.ts`).
 */

import { useState } from "react";

import {
  PROVIDER_COLOR,
  describeCostRange,
  fmtCop,
  fmtUsd,
  rangeDays,
  summarizeTotal,
  type CostRange,
  type TotalEntry,
} from "@plugins/costs/frontend/entities/cost-report";

import { readExcluded, toggled, writeExcluded } from "../model/selection";

interface Props {
  entries: TotalEntry[];
  range: CostRange;
  fxNote?: string | null;
}

/** Lo que dice cada casilla a la derecha del nombre: su cifra o por qué no hay. */
function entryHint(entry: TotalEntry): string {
  if (entry.state === "loading") return "consultando…";
  const report = entry.report;
  if (entry.state === "failed" || !report || report.status === "error") return "sin dato";
  if (report.status === "not_connected") return "sin conectar";
  return report.productionUsdMicros !== null ? fmtUsd(report.productionUsdMicros) : "sin tasa";
}

export function CostTotalCard({ entries, range, fxNote }: Props) {
  const [excluded, setExcluded] = useState<Set<string>>(() => readExcluded());
  const choose = (next: Set<string>) => {
    setExcluded(next);
    writeExcluded(next);
  };
  const selected = entries.filter((e) => !excluded.has(e.id));
  const allSelected = selected.length === entries.length;
  const total = summarizeTotal(selected);
  const perDay = Math.round(total.usdMicros / Math.max(rangeDays(range), 1));

  return (
    <section
      aria-label="Total del sistema productivo"
      className="flex flex-col gap-3 rounded-lg border border-line bg-win-bg p-4"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 className="m-0 text-[13px] font-semibold text-fg">
          {allSelected
            ? "Total del sistema productivo"
            : `Total de lo seleccionado (${selected.length} de ${entries.length})`}
        </h2>
        <p className="m-0 text-[12px] text-fg-muted">{describeCostRange(range)}</p>
      </header>

      <div className="flex flex-wrap items-end gap-x-5 gap-y-1">
        <p data-testid="costs-total-usd" className="m-0 text-[28px] font-semibold leading-none text-fg tabular-nums">
          {fmtUsd(total.usdMicros)}
        </p>
        <p data-testid="costs-total-cop" className="m-0 text-[14px] text-fg-soft tabular-nums">
          ≈ {fmtCop(total.copMicros)}
        </p>
        {total.usdMicros > 0 ? (
          <p className="m-0 text-[12px] text-fg-muted tabular-nums">
            Promedio por día: {fmtUsd(perDay)}
          </p>
        ) : null}
      </div>

      <fieldset className="m-0 flex flex-col gap-1.5 border-0 p-0">
        <legend className="mb-1 p-0 text-[11.5px] font-semibold text-fg-muted">
          Qué sumar en el total
        </legend>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
          {entries.map((entry) => {
            const checked = !excluded.has(entry.id);
            return (
              <label
                key={entry.id}
                className={
                  "flex cursor-pointer items-center gap-1.5 text-[12px] tabular-nums " +
                  (checked ? "text-fg-soft" : "text-fg-faint")
                }
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => choose(toggled(excluded, entry.id))}
                  className="h-3.5 w-3.5 accent-accent"
                />
                <span aria-hidden className={`h-2 w-2 rounded-full ${PROVIDER_COLOR[entry.id] ?? "bg-accent"}`} />
                <span>{entry.label}</span>
                <span className="text-fg-muted">{entryHint(entry)}</span>
              </label>
            );
          })}
          {!allSelected ? (
            <button
              type="button"
              onClick={() => choose(new Set())}
              className="rounded-md border border-line-strong px-2 py-0.5 text-[11.5px] font-semibold text-fg hover:bg-line focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              Marcar todos
            </button>
          ) : null}
        </div>
      </fieldset>

      {!total.complete ? (
        <p className="m-0 rounded-md bg-warn-soft px-2.5 py-1.5 text-[12px] text-warn">
          Parcial — no suma:{" "}
          {total.missing.map((m) => `${m.label}: ${m.reason}`).join(" · ")}
        </p>
      ) : null}

      {total.usdMicros > 0 ? (
        <div className="flex flex-col gap-2">
          <div
            className="flex h-2.5 w-full overflow-hidden rounded-full bg-line"
            role="img"
            aria-label="Participación de cada proveedor en el total"
          >
            {total.parts
              .filter((p) => p.usdMicros > 0)
              .map((p) => (
                <span
                  key={p.id}
                  className={`h-full ${PROVIDER_COLOR[p.id] ?? "bg-accent"}`}
                  style={{ width: `${(p.usdMicros / total.usdMicros) * 100}%` }}
                  title={`${p.label}: ${fmtUsd(p.usdMicros)}`}
                />
              ))}
          </div>
          <ul className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-[12px] text-fg-soft">
            {total.parts.map((p) => (
              <li key={p.id} className="flex items-center gap-1.5 tabular-nums">
                <span aria-hidden className={`h-2 w-2 rounded-full ${PROVIDER_COLOR[p.id] ?? "bg-accent"}`} />
                {p.label}: {Math.round((p.usdMicros / total.usdMicros) * 100)} %
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {fxNote ? <p className="m-0 text-[11.5px] text-fg-faint">{fxNote}</p> : null}
    </section>
  );
}
