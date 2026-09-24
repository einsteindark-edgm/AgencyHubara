/**
 * Sección Laboratorio (plan LABORATORIO_CONVERSACIONES_PLAN.md §11, diseño
 * aprobado 2026-09-23 §09). Se parece a Chats pero no tiene compositor: su
 * única acción es lanzar o cancelar una corrida, que no toca a ningún cliente.
 *
 * Barra: la corrida elegida y el botón "Nueva corrida". Pestañas:
 * Conversaciones y Banco y corridas (Resumen llega con los bots simulados).
 * Los Pages no reciben props (PluginHost); los datos vienen de la entity
 * local, que solo llama a `/api/lab/*`.
 */

import { useState } from "react";

import { BenchRuns } from "@plugins/lab/frontend/features/bench-runs";
import { LabConversations } from "@plugins/lab/frontend/features/lab-conversations";
import { RunLauncher } from "@plugins/lab/frontend/features/run-launcher";
import { apiErrorDetail, useLabRuns, type LabRun } from "@plugins/lab/frontend/entities/lab-run";

type Tab = "conv" | "banco";

const TABS: Array<[Tab, string]> = [
  ["conv", "Conversaciones"],
  ["banco", "Banco y corridas"],
];

function runMeta(run: LabRun): string {
  const bots = run.arms.length;
  return [run.bench_id, `${bots} ${bots === 1 ? "bot" : "bots"}`, run.reps ? `${run.reps} ${run.reps === 1 ? "repetición" : "repeticiones"}` : ""].filter(Boolean).join(" · ");
}

export default function LabPage() {
  const runs = useLabRuns();
  const list = runs.data?.runs ?? [];
  const [picked, setPicked] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("conv");
  const run = list.find((r) => r.run_id === picked) ?? list[0] ?? null;
  const error = runs.isError ? apiErrorDetail(runs.error) : null;
  // 404 en /api/lab/runs = el API de este entorno no tiene el plugin prendido
  // (ENABLED_PLUGINS del deploy); el build del dashboard trae todas las secciones.
  const errorText =
    error?.status === 404
      ? "El laboratorio no está habilitado en este entorno (el plugin lab está apagado en el API)."
      : (error?.message ?? "No se pudieron leer las corridas del laboratorio.");

  return (
    <section className="flex h-full min-h-0 flex-col overflow-hidden bg-win-bg text-fg" aria-label="Laboratorio">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line bg-titlebar px-4 py-2.5">
        <h1 className="m-0 text-[13px] font-semibold">Laboratorio · Agente de ventas</h1>
        {list.length > 0 ? (
          <label className="ml-auto flex items-center gap-2 text-xs text-fg-muted">
            Corrida
            <select
              aria-label="Corrida"
              value={run?.run_id ?? ""}
              onChange={(e) => setPicked(e.target.value)}
              className="rounded-md border border-line-strong bg-canvas px-2 py-1 font-mono text-[11.5px] text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              {list.map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {r.run_id}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span className="ml-auto" />
        )}
        {run ? <span className="text-xs tabular-nums text-fg-muted">{runMeta(run)}</span> : null}
        {runs.isSuccess ? <RunLauncher lastBenchId={list.find((r) => r.bench_id)?.bench_id ?? null} /> : null}
      </div>

      <div role="tablist" aria-label="Pestañas del laboratorio" className="flex gap-0.5 overflow-x-auto border-b border-line bg-inspector px-3">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            id={`lab-tab-${key}`}
            aria-selected={tab === key}
            aria-controls="lab-tab-panel"
            onClick={() => setTab(key)}
            className={
              "whitespace-nowrap border-0 border-b-2 bg-transparent px-2.5 py-[11px] text-[12.5px] font-medium leading-none focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent " +
              (tab === key ? "border-accent text-fg" : "border-transparent text-fg-muted")
            }
          >
            {label}
          </button>
        ))}
      </div>

      <div role="tabpanel" id="lab-tab-panel" aria-labelledby={`lab-tab-${tab}`} className="min-h-0 flex-1 overflow-auto bg-canvas">
        {runs.isPending ? <p className="p-4 text-[12.5px] text-fg-muted">Cargando las corridas…</p> : null}
        {error ? <p className="p-4 text-[12.5px] text-fg-muted">{errorText}</p> : null}
        {runs.isSuccess && !run ? <p className="p-4 text-[12.5px] text-fg-muted">Todavía no hay corridas. Usa Nueva corrida para lanzar la primera.</p> : null}
        {run && tab === "conv" ? <LabConversations key={run.run_id} run={run} /> : null}
        {run && tab === "banco" ? <BenchRuns runs={list} selectedRun={run.run_id} onSelectRun={setPicked} /> : null}
      </div>
    </section>
  );
}
