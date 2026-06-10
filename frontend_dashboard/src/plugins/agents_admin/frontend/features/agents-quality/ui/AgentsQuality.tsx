import { EvalTrendChart } from "@plugins/agents_admin/frontend/features/eval-trend-chart";
import { GoldenEvalCuration } from "@plugins/agents_admin/frontend/features/golden-eval-curation";

/**
 * Panel "Calidad LLM" del agente de ventas. Vive como tab del canvas central
 * (ver `agents-prompts`), visible solo para el agente `sales` — el único con
 * harness de evaluación hoy.
 *
 * Compone las dos superficies del eval loop, que leen `/api/agents/evals/*`:
 *   1. Tendencia de calidad: score por métrica en el tiempo (registro de días
 *      bajos y recuperación), alimentada por el eval diario online.
 *   2. Curación de goldens: las conversaciones reales que puntuaron bajo, para
 *      revisar / aprobar como golden.
 *
 * Nota FSD: composición intra-plugin (feature → feature del MISMO plugin), que
 * `dependency-cruiser` permite — la regla `plugins-no-cross-plugin` solo veta el
 * acoplamiento entre plugins distintos.
 */
export function AgentsQuality() {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden p-3 text-fg">
      <div className="shrink-0">
        <EvalTrendChart />
      </div>
      <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-line">
        <GoldenEvalCuration />
      </div>
    </div>
  );
}

export default AgentsQuality;
