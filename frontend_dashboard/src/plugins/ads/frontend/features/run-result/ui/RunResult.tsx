/**
 * `RunResult` — la vista EN VIVO de un run. Es dueña de la data del run
 * (`useRun`) y monta la suscripción al stream (`useAdAnalysisRunEvents`): así
 * el status, el timeline de eventos y el resultado/error se actualizan solos a
 * medida que el backend republica los cambios al dominio `ads`.
 *
 * Cuando el run está en `awaiting_approval`, la decisión humana (aprobar/
 * rechazar) es OTRA feature (`hitl-decision`). FSD prohíbe que una feature
 * importe a otra, así que el page la compone y la pasa por `decisionSlot` — esta
 * feature solo decide DÓNDE va (junto al contexto), no la implementa.
 */
import type { ReactNode } from "react";

import { Icon } from "@/shared/ui";

import {
  useAdAnalysisRunEvents,
  useRun,
} from "@plugins/ads/frontend/entities/ad-analysis-run";

import { statusMeta } from "../model/statusMeta";

interface Props {
  runId: string | null;
  /** Lo llena el page con <HitlDecision> cuando status === awaiting_approval. */
  decisionSlot?: ReactNode;
}

export function RunResult({ runId, decisionSlot }: Props) {
  // Push-first: el stream invalida el detalle; `useRun` refetchea el record.
  useAdAnalysisRunEvents(runId);
  const { data: run, isLoading, isError } = useRun(runId);

  if (!runId) {
    return (
      <section className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center text-fg-muted">
        <span className="text-fg-faint">
          <Icon.bot />
        </span>
        <div className="text-sm font-semibold text-fg-soft">Ningún run activo</div>
        <div className="text-xs">
          Dispará un agente para ver su progreso en vivo acá.
        </div>
      </section>
    );
  }

  if (isLoading && !run) {
    return (
      <section className="flex flex-col gap-3 p-4 text-fg">
        <div className="text-sm text-fg-muted">Cargando run…</div>
      </section>
    );
  }

  if (isError || !run) {
    return (
      <section className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center text-fg-muted">
        <span className="text-danger">
          <Icon.alert />
        </span>
        <div className="text-sm font-semibold text-fg-soft">
          No se pudo cargar el run
        </div>
        <div className="text-xs">
          El backend no respondió o devolvió un formato inesperado.
        </div>
      </section>
    );
  }

  const meta = statusMeta(run.status);

  return (
    <section className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4 text-fg">
      <header className="flex items-center justify-between gap-3">
        <div className="flex flex-col">
          <span className="text-sm font-semibold">{run.agent}</span>
          <span className="font-mono text-xs text-fg-faint">{run.runId}</span>
        </div>
        <span
          className="flex items-center gap-1.5 text-xs font-semibold"
          style={{ color: meta.color }}
        >
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: meta.color }}
          />
          {meta.label}
        </span>
      </header>

      {run.status === "awaiting_approval" && decisionSlot}

      <div className="flex flex-col gap-2">
        <div className="text-xs font-medium uppercase tracking-wide text-fg-muted">
          Eventos
        </div>
        {run.events.length === 0 ? (
          <div className="text-xs text-fg-faint">Sin eventos todavía…</div>
        ) : (
          <ol className="flex flex-col gap-2">
            {run.events.map((e) => (
              <li
                key={e.eventId}
                className="rounded-md border border-line bg-canvas p-2"
              >
                <span className="font-mono text-xs font-semibold text-accent">
                  {e.type}
                </span>
                {e.payload !== undefined && e.payload !== null && (
                  <pre className="mt-1 max-h-40 overflow-auto font-mono text-[11px] text-fg-soft">
                    {formatJson(e.payload)}
                  </pre>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>

      {run.status === "completed" && (
        <div className="flex flex-col gap-2">
          <div className="text-xs font-medium uppercase tracking-wide text-ok">
            Resultado
          </div>
          <pre className="max-h-72 overflow-auto rounded-md border border-line bg-canvas p-2 font-mono text-xs text-fg-soft">
            {formatJson(run.result)}
          </pre>
        </div>
      )}

      {run.status === "failed" && (
        <div className="flex flex-col gap-2">
          <div className="text-xs font-medium uppercase tracking-wide text-danger">
            Error
          </div>
          <pre className="max-h-72 overflow-auto rounded-md border border-danger/40 bg-danger-soft p-2 font-mono text-xs text-fg-soft">
            {formatJson(run.error)}
          </pre>
        </div>
      )}
    </section>
  );
}

function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value ?? null, null, 2);
  } catch {
    return String(value);
  }
}
