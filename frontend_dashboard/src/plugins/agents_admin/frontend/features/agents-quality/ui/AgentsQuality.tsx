import { useState } from "react";

import { useConversationEvals } from "@plugins/agents_admin/frontend/entities/episode-eval";
import { EpisodeEvals } from "@plugins/agents_admin/frontend/features/episode-evals";
import { EvalTrendChart } from "@plugins/agents_admin/frontend/features/eval-trend-chart";
import { GoldenEvalCuration } from "@plugins/agents_admin/frontend/features/golden-eval-curation";
import { Icon } from "@/shared/ui";

/** Ventana compartida (días) entre la tendencia y la lista de episodios — el
 *  mismo `useConversationEvals(WINDOW_DAYS, "online")` alimenta a ambos (una
 *  sola fetch, cache de TanStack). */
const WINDOW_DAYS = 30;

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
 *   1. **Tendencia** — valor inicial→actual (con fecha) por métrica. Selector
 *      de conversación: "Todas" promedia por día; un episodio aísla SU evolución
 *      sin contaminación. Los días bajos (modo Todas) filtran la lista.
 *   2. **Episodios** — la vista central: qué conversación puntuó qué, su timeline
 *      de evals, por qué falló cada métrica, y si es candidata a golden.
 *   3. **Goldens** — curación de candidatos (aprobar → regresión en CI).
 *
 * Nota FSD: composición intra-plugin (feature → feature del MISMO plugin), que
 * `dependency-cruiser` permite — la regla `plugins-no-cross-plugin` solo veta el
 * acoplamiento entre plugins distintos. El estado compartido entre superficies
 * (episodio aislado, día seleccionado, candidato a abrir) vive ACÁ, lifted: las
 * features hermanas no se hablan entre sí, reciben callbacks del padre. El
 * episodio seleccionado es UNO: lo fija tanto el selector de la tendencia como
 * un click en la lista, y ambas superficies lo reflejan.
 */
export function AgentsQuality() {
  const [view, setView] = useState<"episodios" | "goldens">("episodios");
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [selectedEpisodeKey, setSelectedEpisodeKey] = useState<string | null>(null);
  const [goldenToOpen, setGoldenToOpen] = useState<string | null>(null);
  const [onlyFailing, setOnlyFailing] = useState(false);

  // Alerta de calidad: cuántos episodios evaluados están bajo el umbral (la
  // última eval falló alguna métrica). Es el "te aviso de los malos" que pediste
  // — siempre visible en la nav, derivado del mismo query (cache compartido).
  const { data: convData } = useConversationEvals(WINDOW_DAYS, "online");
  const failingCount = (convData?.conversations ?? []).filter(
    (c) => !c.last_passed,
  ).length;

  const openCandidate = (candidateId: string) => {
    setGoldenToOpen(candidateId);
    setView("goldens");
  };

  const showFailing = () => {
    setOnlyFailing(true);
    setView("episodios");
  };

  // Día y episodio son filtros mutuamente excluyentes de la misma vista.
  const selectDate = (date: string | null) => {
    setSelectedDate(date);
    if (date) {
      setSelectedEpisodeKey(null);
      setView("episodios");
    }
  };

  const selectEpisode = (key: string | null) => {
    setSelectedEpisodeKey(key);
    if (key) {
      setSelectedDate(null);
      setView("episodios");
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden p-3 text-fg">
      <div className="shrink-0">
        <EvalTrendChart
          windowDays={WINDOW_DAYS}
          selectedDate={selectedDate}
          onSelectDate={selectDate}
          selectedEpisodeKey={selectedEpisodeKey}
          onSelectEpisode={selectEpisode}
        />
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
          {failingCount > 0 && (
            <button
              type="button"
              onClick={showFailing}
              className="ml-auto inline-flex items-center gap-1.5 rounded-md bg-red/15 px-3 py-1.5 text-xs font-semibold text-red transition hover:bg-red/25"
              title="Ver los episodios cuya última evaluación falló alguna métrica"
            >
              <Icon.alert /> {failingCount} episodio{failingCount > 1 ? "s" : ""} bajo el umbral
            </button>
          )}
        </nav>
        <div className="min-h-0 flex-1 overflow-hidden">
          {view === "episodios" ? (
            <EpisodeEvals
              windowDays={WINDOW_DAYS}
              dateFilter={selectedDate}
              onClearDateFilter={() => setSelectedDate(null)}
              selectedKey={selectedEpisodeKey}
              onSelectKey={selectEpisode}
              onlyFailing={onlyFailing}
              onOnlyFailingChange={setOnlyFailing}
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
