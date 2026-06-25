/**
 * `GraphAgentsSection` — la Page del plugin graphagents.
 *
 * Buzón del dashboard para los agentes de GraphAgents: el operador dispara un
 * agente (feature `trigger-run`), ve su progreso EN VIVO (feature `run-result`,
 * que monta la suscripción al stream) y, si el run pide aprobación humana,
 * decide (feature `hitl-decision`). Equivalente al viewer de GraphAgents pero
 * embebido en el dashboard de prod.
 *
 * FSD: el Page solo ORQUESTA — compone features y sostiene el estado de
 * coordinación (`activeRunId`). No hace fetch directo ni lógica de negocio:
 * - el run activo vive en el PluginHost (`useSelection`, contrato F5/F7 — los
 *   Pages NO reciben props del shell);
 * - el record del run lo lee la entity (`useRun`) para saber si mostrar el
 *   panel HITL y con qué contexto;
 * - `trigger-run` no puede importar `run-result` ni `hitl-decision` (cross-
 *   feature prohibido): el Page es el único punto que los une.
 */
import { usePluginHost, useSelection } from "@/shared/lib";

import { useRun } from "@plugins/graphagents/frontend/entities/graphagents-run";
import { TriggerRun } from "@plugins/graphagents/frontend/features/trigger-run";
import { RunResult } from "@plugins/graphagents/frontend/features/run-result";
import { HitlDecision } from "@plugins/graphagents/frontend/features/hitl-decision";

export function GraphAgentsSection() {
  const { showInspector } = usePluginHost();
  // El run activo es la selección del plugin (fallback "" = ninguno). El shell
  // no siembra ids — el fallback lo declara el plugin (regla #4 de estado).
  const [activeRunId, setActiveRunId] = useSelection("graphagents");
  const runId = activeRunId || null;

  // Record del run activo — para decidir si el page monta el panel HITL y con
  // qué contexto. Comparte el query key con `RunResult` (TanStack dedupe): una
  // sola request, una sola suscripción.
  const { data: run } = useRun(runId);

  const decision =
    run?.status === "awaiting_approval" && runId ? (
      <HitlDecision runId={runId} awaiting={run.awaiting} />
    ) : undefined;

  return (
    <main className="flex min-h-0 flex-1 overflow-hidden bg-canvas">
      <div className="flex min-h-0 shrink-0 overflow-y-auto border-r border-line">
        <TriggerRun onRunStarted={setActiveRunId} />
      </div>
      {showInspector && (
        <div className="flex min-h-0 flex-1 overflow-hidden">
          <RunResult runId={runId} decisionSlot={decision} />
        </div>
      )}
    </main>
  );
}

export default GraphAgentsSection;
