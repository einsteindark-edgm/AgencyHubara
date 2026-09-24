/**
 * Pestaña "Banco y corridas" (diseño §09): qué entró al banco de la corrida
 * elegida, qué se excluyó y por qué (agrupado por motivo; sin teléfonos), y
 * cada corrida con sus bots, su estado y su costo.
 */

import { armLabel, formatUsd, useRunBench, type LabRun } from "@plugins/lab/frontend/entities/lab-run";

interface Props {
  runs: LabRun[];
  selectedRun: string | null;
  onSelectRun: (runId: string) => void;
}

const REASON_LABEL: Record<string, string> = {
  turno_del_sistema: "Turno del sistema (no lo escribió el cliente)",
  sin_metadata: "Conversación sin metadata",
  golden: "Sesión del golden suite",
  sesion_de_prueba: "Sesión de prueba",
  numero_interno: "Número interno",
};

const PHASE_LABEL: Record<string, string> = {
  preparing: "Preparando",
  running: "Corriendo",
  done: "Terminada",
  failed: "Falló",
  cancelled: "Cancelada",
};

const TH = "whitespace-nowrap border-b border-line bg-white/[0.03] px-2.5 py-2 text-left text-[10px] font-semibold uppercase leading-tight tracking-[0.08em] text-fg-faint";
const TD = "border-b border-line px-2.5 py-2 align-middle text-fg-soft";
const TABLE = "mt-3 w-full border-collapse overflow-hidden rounded-[10px] border border-line bg-inspector text-[12.5px]";

function phaseText(run: LabRun): string {
  const label = run.phase ? (PHASE_LABEL[run.phase] ?? run.phase) : "Sin estado";
  return run.phase === "failed" && run.error ? `${label}: ${run.error}` : label;
}

function when(ms: number | null): string {
  if (ms === null) return "—";
  return new Intl.DateTimeFormat("es-CO", { timeZone: "America/Bogota", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(ms));
}

export function BenchRuns({ runs, selectedRun, onSelectRun }: Props) {
  const bench = useRunBench(selectedRun);
  const byReason = new Map<string, number>();
  for (const e of bench.data?.exclusions ?? []) byReason.set(e.reason, (byReason.get(e.reason) ?? 0) + 1);

  return (
    <div className="bg-canvas p-4">
      {bench.isPending && selectedRun ? <p className="text-[12.5px] text-fg-muted">Cargando el banco…</p> : null}
      {bench.isError ? <p className="text-[12.5px] text-fg-muted">Esta corrida todavía no publicó su banco.</p> : null}
      {bench.data ? (
        <>
          <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-2.5">
            <Tile value={bench.data.counts.sessions} label="conversaciones" />
            <Tile value={bench.data.counts.cases} label="turnos del cliente en el banco" />
            <Tile value={bench.data.counts.excluded_turns} label="excluidos, con su motivo" />
          </div>
          {byReason.size > 0 ? (
            <div className="overflow-x-auto">
              <table aria-label="Exclusiones del banco" className={TABLE}>
                <thead>
                  <tr>
                    <th className={TH}>Motivo</th>
                    <th className={TH + " text-right"}>Cantidad</th>
                  </tr>
                </thead>
                <tbody>
                  {[...byReason.entries()]
                    .sort((a, b) => b[1] - a[1])
                    .map(([reason, n]) => (
                      <tr key={reason}>
                        <td className={TD}>{REASON_LABEL[reason] ?? reason}</td>
                        <td className={TD + " text-right tabular-nums"}>{n}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </>
      ) : null}

      <div className="overflow-x-auto">
        <table aria-label="Corridas" className={TABLE}>
          <thead>
            <tr>
              <th className={TH}>Corrida</th>
              <th className={TH}>Inicio</th>
              <th className={TH}>Bots</th>
              <th className={TH + " text-right"}>Repeticiones</th>
              <th className={TH}>Estado</th>
              <th className={TH + " text-right"}>Costo</th>
              <th className={TH}>
                <span className="sr-only">Acciones</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => {
              const on = run.run_id === selectedRun;
              return (
                <tr key={run.run_id} aria-current={on ? "true" : undefined} className={on ? "bg-accent-soft/40" : undefined}>
                  <td className={TD + " font-mono text-[11.5px] text-fg"}>{run.run_id}</td>
                  <td className={TD + " whitespace-nowrap tabular-nums"}>{when(run.started_at_ms)}</td>
                  <td className={TD}>{run.arms.map(armLabel).join(" · ") || "—"}</td>
                  <td className={TD + " text-right tabular-nums"}>{run.reps ?? "—"}</td>
                  <td className={TD + (run.phase === "failed" ? " text-danger" : "")}>{phaseText(run)}</td>
                  <td className={TD + " text-right tabular-nums"}>{formatUsd(run.spent_usd)}</td>
                  <td className={TD + " text-right"}>
                    <button
                      type="button"
                      disabled={on}
                      onClick={() => onSelectRun(run.run_id)}
                      className="rounded-md border border-line-strong px-2 py-1 text-[11.5px] text-fg-soft disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    >
                      Ver
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2.5 text-[11.5px] text-fg-muted">
        Cada turno excluido tiene su motivo. Las conversaciones de prueba, del golden suite y de números internos no entran al banco.
      </p>
    </div>
  );
}

function Tile({ value, label }: { value: number; label: string }) {
  return (
    <div className="rounded-[10px] border border-line bg-inspector p-3">
      <b className="block text-[22px] font-semibold leading-tight tabular-nums text-fg">{value.toLocaleString("es-CO")}</b>
      <span className="text-[11.5px] text-fg-muted">{label}</span>
    </div>
  );
}
