/**
 * Tarjeta de UN proveedor en el rango: cuánto costó lo productivo, de dónde
 * sale la cifra, cuánto por día, el desglose (por llave, categoría, servicio
 * o medidor) y aparte lo que no es producción (laboratorios, simulaciones).
 *
 * Estados, cada uno visible (L-10/L-30): consultando · nuestro API falló ·
 * el proveedor falló · sin conectar (con el cómo) · con datos. Nunca un $0
 * por falta de dato. «Actualizar» le pregunta de nuevo al proveedor.
 */

import { useId } from "react";

import {
  SOURCE_LABEL,
  costErrorDetail,
  fmtCop,
  fmtMoney,
  fmtUsd,
  useRefreshProviderReport,
  type CostLine,
  type CostProvider,
  type CostRange,
  type ProviderReport,
} from "@plugins/costs/frontend/entities/cost-report";

export interface ProviderQueryState {
  data: ProviderReport | undefined;
  isPending: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
}

interface Props {
  provider: CostProvider;
  query: ProviderQueryState;
  range: CostRange;
}

const QTY = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 2 });

const BUTTON =
  "rounded-md border border-line-strong px-2.5 py-1 text-[12px] font-semibold text-fg " +
  "hover:bg-line disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 " +
  "focus-visible:outline-accent";

function Badge({ tone, children }: { tone: "ok" | "warn" | "danger" | "neutral"; children: string }) {
  const cls = {
    ok: "bg-ok-soft text-ok",
    warn: "bg-warn-soft text-warn",
    danger: "bg-danger-soft text-danger",
    neutral: "bg-line text-fg-soft",
  }[tone];
  return <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${cls}`}>{children}</span>;
}

function fetchedLabel(report: ProviderReport): string | null {
  if (!report.fetchedAt) return null;
  const at = new Date(report.fetchedAt).toLocaleTimeString("es-CO", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/Bogota",
  });
  return report.cached ? `Consultado a las ${at} (guardado)` : `Consultado a las ${at}`;
}

/** «22 oct» — día calendario sin tocar zona horaria (el ISO ya es un día). */
function shortDay(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("es-CO", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}

function DailyBars({ daily }: { daily: ProviderReport["daily"] }) {
  const max = Math.max(...daily.map((d) => d.usdMicros), 0);
  if (daily.length === 0 || max === 0) return null;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex h-14 items-end gap-[2px]" role="img" aria-label="Costo productivo por día">
        {daily.map((d) => (
          <span
            key={d.date}
            data-testid="costs-day-bar"
            className="min-w-[3px] flex-1 rounded-t-sm bg-accent"
            style={{ height: `${Math.max((d.usdMicros / max) * 100, d.usdMicros > 0 ? 4 : 1)}%` }}
            title={`${shortDay(d.date)}: ${fmtUsd(d.usdMicros)}`}
          />
        ))}
      </div>
      <div className="flex justify-between text-[10.5px] text-fg-faint tabular-nums">
        <span>{shortDay(daily[0].date)}</span>
        <span>Día más alto: {fmtUsd(max)}</span>
        <span>{shortDay(daily[daily.length - 1].date)}</span>
      </div>
    </div>
  );
}

function quantityText(line: CostLine): string | null {
  if (line.quantity === null || !line.unit) return null;
  return `${QTY.format(line.quantity)} ${line.unit}`;
}

function LineRows({ lines }: { lines: CostLine[] }) {
  const groups = new Map<string, CostLine[]>();
  for (const line of lines) {
    const g = line.group ?? "";
    groups.set(g, [...(groups.get(g) ?? []), line]);
  }
  return (
    <div className="flex flex-col gap-2">
      {[...groups.entries()].map(([group, rows]) => (
        <div key={group || "_"} className="flex flex-col">
          {group ? (
            <p className="m-0 pb-0.5 text-[11px] font-semibold uppercase tracking-wide text-fg-faint">{group}</p>
          ) : null}
          <ul className="m-0 flex list-none flex-col p-0">
            {rows.map((line) => (
              <li
                key={line.key}
                className="flex flex-wrap items-baseline justify-between gap-x-3 border-b border-line py-1 text-[12.5px] last:border-b-0"
              >
                <span className="min-w-0 flex-1 break-words text-fg [overflow-wrap:anywhere]">
                  {line.label}
                  {quantityText(line) ? (
                    <span className="ml-2 text-[11.5px] text-fg-muted">{quantityText(line)}</span>
                  ) : null}
                </span>
                <span className="flex shrink-0 items-baseline gap-2 tabular-nums">
                  {line.currency !== "USD" ? (
                    <span className="text-[11.5px] text-fg-muted">{fmtMoney(line.amountMicros, line.currency)}</span>
                  ) : null}
                  <span className="font-semibold text-fg">
                    {line.usdMicros !== null ? fmtUsd(line.usdMicros) : "sin tasa"}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function Body({ provider, report }: { provider: CostProvider; report: ProviderReport }) {
  const apartId = useId();
  if (report.status === "not_connected") {
    return (
      <div className="flex flex-col gap-2 text-[12.5px]">
        {report.howToConnect ? <p className="m-0 text-fg-soft">{report.howToConnect}</p> : null}
        {report.notes.map((n) => (
          <p key={n} className="m-0 text-[12px] text-fg-muted">
            {n}
          </p>
        ))}
      </div>
    );
  }
  const production = report.lines.filter((l) => l.production);
  const apart = report.lines.filter((l) => !l.production);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-x-3 gap-y-1">
        <p
          data-testid={`costs-card-${provider.id}-usd`}
          className="m-0 text-[22px] font-semibold leading-none text-fg tabular-nums"
        >
          {report.productionUsdMicros !== null ? fmtUsd(report.productionUsdMicros) : "Sin total"}
        </p>
        {report.productionCopMicros !== null ? (
          <p className="m-0 text-[12.5px] text-fg-soft tabular-nums">≈ {fmtCop(report.productionCopMicros)}</p>
        ) : null}
      </div>
      <DailyBars daily={report.daily} />
      {production.length > 0 ? (
        <LineRows lines={production} />
      ) : (
        <p className="m-0 text-[12px] text-fg-muted">Sin gasto productivo en el rango.</p>
      )}
      {apart.length > 0 ? (
        <div
          role="group"
          aria-labelledby={apartId}
          className="flex flex-col gap-1 rounded-md bg-canvas px-2.5 py-2 opacity-80"
        >
          <p id={apartId} className="m-0 text-[11.5px] font-semibold text-fg-muted">
            Fuera de producción (no suma al total)
            {report.otherUsdMicros !== null ? `: ${fmtUsd(report.otherUsdMicros)}` : ""}
          </p>
          <LineRows lines={apart} />
        </div>
      ) : null}
      {report.warnings.map((w) => (
        <p key={w} className="m-0 rounded-md bg-warn-soft px-2 py-1 text-[12px] text-warn">
          {w}
        </p>
      ))}
      {report.notes.map((n) => (
        <p key={n} className="m-0 text-[11.5px] text-fg-muted">
          {n}
        </p>
      ))}
    </div>
  );
}

export function ProviderCostCard({ provider, query, range }: Props) {
  const titleId = useId();
  const refresh = useRefreshProviderReport(provider.id, range);
  const report = query.data;

  let badge: { tone: "ok" | "warn" | "danger" | "neutral"; text: string } | null = null;
  if (report?.status === "not_connected") badge = { tone: "neutral", text: "Sin conectar" };
  else if (report?.status === "error") badge = { tone: "danger", text: "Error" };
  else if (report?.source) badge = { tone: "ok", text: SOURCE_LABEL[report.source] };

  return (
    <article
      aria-labelledby={titleId}
      className="flex min-w-0 flex-col gap-3 rounded-lg border border-line bg-win-bg p-4"
    >
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id={titleId} className="m-0 text-[13px] font-semibold text-fg">
            {provider.label}
          </h2>
          {badge ? <Badge tone={badge.tone}>{badge.text}</Badge> : null}
        </div>
        <button
          type="button"
          className={BUTTON}
          disabled={refresh.isPending || query.isPending}
          onClick={() => refresh.mutate()}
        >
          {refresh.isPending ? "Actualizando…" : "Actualizar"}
        </button>
      </header>

      {query.isPending ? (
        <p className="m-0 text-[12.5px] text-fg-muted">Consultando a {provider.label}…</p>
      ) : query.isError || !report ? (
        <div role="alert" className="flex flex-col gap-1">
          <p className="m-0 text-[12.5px] font-semibold text-danger">No se pudo consultar a {provider.label}.</p>
          <p className="m-0 text-[12px] text-fg-muted">{costErrorDetail(query.error)}</p>
          <button type="button" className={`${BUTTON} self-start`} onClick={() => void query.refetch()}>
            Reintentar
          </button>
        </div>
      ) : report.status === "error" ? (
        <div role="alert" className="flex flex-col gap-1">
          <p className="m-0 text-[12.5px] text-danger">{report.error}</p>
          <p className="m-0 text-[12px] text-fg-muted">Esta tarjeta no suma al total. Usa «Actualizar» para reintentar.</p>
        </div>
      ) : (
        <Body provider={provider} report={report} />
      )}

      {refresh.isError ? (
        <p role="status" className="m-0 text-[12px] text-danger">
          No se pudo actualizar: {costErrorDetail(refresh.error)}
        </p>
      ) : null}
      {report && fetchedLabel(report) ? (
        <p className="m-0 text-[11px] text-fg-faint">{fetchedLabel(report)}</p>
      ) : null}
    </article>
  );
}
