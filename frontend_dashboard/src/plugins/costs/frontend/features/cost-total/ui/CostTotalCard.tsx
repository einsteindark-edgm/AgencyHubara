/**
 * Total del sistema productivo en el rango: la suma de lo que cada proveedor
 * ya respondió (solo lo productivo), en dólares y pesos, con la participación
 * de cada uno. Si falta alguno (sin conectar, falló o todavía consultando) el
 * total dice «Parcial» y nombra qué falta — nunca lo cuenta como $0.
 */

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

interface Props {
  entries: TotalEntry[];
  range: CostRange;
  fxNote?: string | null;
}

export function CostTotalCard({ entries, range, fxNote }: Props) {
  const total = summarizeTotal(entries);
  const perDay = Math.round(total.usdMicros / Math.max(rangeDays(range), 1));

  return (
    <section
      aria-label="Total del sistema productivo"
      className="flex flex-col gap-3 rounded-lg border border-line bg-win-bg p-4"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 className="m-0 text-[13px] font-semibold text-fg">Total del sistema productivo</h2>
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
                {p.label}: {fmtUsd(p.usdMicros)} ({Math.round((p.usdMicros / total.usdMicros) * 100)} %)
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {fxNote ? <p className="m-0 text-[11.5px] text-fg-faint">{fxNote}</p> : null}
    </section>
  );
}
