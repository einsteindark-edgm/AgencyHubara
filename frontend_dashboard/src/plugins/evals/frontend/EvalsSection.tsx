import { EvalTrendChart } from "@plugins/evals/frontend/features/eval-trend-chart";
import { GoldenEvalCuration } from "@plugins/evals/frontend/features/golden-eval-curation";

/** El shell pasa un "bandejón" de props cross-plugin; esta sección las ignora. */
export type EvalsSectionProps = Record<string, unknown>;

/**
 * Sección "Calidad LLM" del dashboard:
 *  - Tendencia de calidad: scores por métrica en el tiempo (registro de días bajos
 *    y recuperación) — alimentada por el eval diario sobre conversaciones reales.
 *  - Curación de goldens: las conversaciones reales que puntuaron bajo, para revisar.
 */
export function EvalsSection(_props: EvalsSectionProps) {
  return (
    <div className="flex h-full min-h-0 flex-col gap-4 p-1">
      <EvalTrendChart />
      <div className="min-h-0 flex-1">
        <GoldenEvalCuration />
      </div>
    </div>
  );
}

export default EvalsSection;
