import { GoldenEvalCuration } from "@plugins/evals/frontend/features/golden-eval-curation";

/** El shell pasa un "bandejón" de props cross-plugin; esta sección las ignora. */
export type EvalsSectionProps = Record<string, unknown>;

/**
 * Sección "Calidad LLM" del dashboard — pestaña de curación de goldens del
 * harness de evaluación del Asesor de Ventas.
 */
export function EvalsSection(_props: EvalsSectionProps) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <GoldenEvalCuration />
    </div>
  );
}

export default EvalsSection;
