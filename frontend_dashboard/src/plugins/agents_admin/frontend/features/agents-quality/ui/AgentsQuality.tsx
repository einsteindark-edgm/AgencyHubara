import { useState } from "react";

import { EpisodeEvals } from "@plugins/agents_admin/frontend/features/episode-evals";
import { EvalTrendChart } from "@plugins/agents_admin/frontend/features/eval-trend-chart";
import { GoldenEvalCuration } from "@plugins/agents_admin/frontend/features/golden-eval-curation";
import { Icon } from "@/shared/ui";

/**
 * Panel "Calidad LLM" del agente de ventas. Vive como tab del canvas central
 * (ver `agents-prompts`), visible solo para el agente `sales` — el único con
 * harness de evaluación hoy.
 *
 * El eval loop en una frase: un eval online corre varias veces al día, puntúa
 * CADA EPISODIO de conversación real con un juez LLM, y los episodios que caen
 * bajo el umbral se vuelven candidatos a golden (regresión de CI). Las tres
 * superficies (todas leen `/api/agents/evals/*`):
 *
 *   1. **Tendencia** — promedio por métrica por día. Click en un día → filtra
 *      la vista de episodios a ese día (trazar el día bajo hasta SU conversación).
 *   2. **Episodios** — la vista central: qué conversación puntuó qué, su
 *      timeline de evals (¿mejoró o quedó igual?), por qué falló cada métrica,
 *      y si es candidata a golden.
 *   3. **Goldens** — curación de candidatos (aprobar → regresión en CI).
 *
 * Nota FSD: composición intra-plugin (feature → feature del MISMO plugin), que
 * `dependency-cruiser` permite — la regla `plugins-no-cross-plugin` solo veta el
 * acoplamiento entre plugins distintos. El estado compartido entre superficies
 * (día seleccionado, candidato a abrir) vive ACÁ, lifted: las features hermanas
 * no se hablan entre sí, reciben callbacks del padre.
 */
export function AgentsQuality() {
  const [view, setView] = useState<"episodios" | "goldens">("episodios");
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [goldenToOpen, setGoldenToOpen] = useState<string | null>(null);

  const openCandidate = (candidateId: string) => {
    setGoldenToOpen(candidateId);
    setView("goldens");
  };

  const selectDate = (date: string | null) => {
    setSelectedDate(date);
    if (date) setView("episodios");
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden p-3 text-fg">
      <div className="shrink-0">
        <EvalTrendChart selectedDate={selectedDate} onSelectDate={selectDate} />
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-line">
        <nav className="flex shrink-0 items-center gap-1 border-b border-line p-2">
          <button
            type="button"
            onClick={() => setView("episodios")}
            className={
              "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition " +
              (view === "episodios"
                ? "bg-white/10 text-fg"
                : "text-fg-muted hover:bg-white/5")
            }
          >
            <Icon.timeline /> Episodios evaluados
          </button>
          <button
            type="button"
            onClick={() => setView("goldens")}
            className={
              "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition " +
              (view === "goldens"
                ? "bg-white/10 text-fg"
                : "text-fg-muted hover:bg-white/5")
            }
          >
            <Icon.shield /> Curación de goldens
          </button>
        </nav>
        <div className="min-h-0 flex-1 overflow-hidden">
          {view === "episodios" ? (
            <EpisodeEvals
              dateFilter={selectedDate}
              onClearDateFilter={() => setSelectedDate(null)}
              onOpenCandidate={openCandidate}
            />
          ) : (
            <GoldenEvalCuration initialSelectedId={goldenToOpen} />
          )}
        </div>
      </div>
    </div>
  );
}

export default AgentsQuality;
