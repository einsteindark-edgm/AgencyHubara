import { useState, type ReactNode } from "react";

import {
  useScorecards,
  type EpisodeRef,
  type ScorecardBot,
} from "@plugins/agents_admin/frontend/entities/scorecard";
import {
  ComplianceMatrix,
  type VerdictFilter,
} from "@plugins/agents_admin/frontend/features/compliance-matrix";
import { EpisodeEvals } from "@plugins/agents_admin/frontend/features/episode-evals";
import { EvalTrendChart } from "@plugins/agents_admin/frontend/features/eval-trend-chart";
import { GoldenEvalCuration } from "@plugins/agents_admin/frontend/features/golden-eval-curation";
import { JudgeCalibration } from "@plugins/agents_admin/frontend/features/judge-calibration";
import { Icon } from "@/shared/ui";

import { ConversationDetail } from "./ConversationDetail";
import { SummaryView } from "./SummaryView";

/** Ventana (días) del scorecard: la MISMA para la alerta, la matriz y los
 *  agregados (8 semanas: la tendencia semanal necesita historia). Una sola
 *  ventana evita que el resumen diga "10 en FALLA" y la matriz muestre 7. */
const WINDOW_DAYS = 56;
const STATS_DAYS = WINDOW_DAYS;
/** Ventana de las métricas legadas (sin cambios respecto de la vista anterior). */
const LEGACY_WINDOW_DAYS = 30;

type Tab = "resumen" | "conversaciones" | "calibracion" | "legado" | "goldens";

/** Durante el encendido del bot nuevo (plan del laboratorio PR 18), las mismas
 *  gráficas separan los episodios de cada bot (el `mode` de su traza). */
const BOT_OPTIONS: ReadonlyArray<{ value: ScorecardBot | null; label: string }> = [
  { value: null, label: "Todos" },
  { value: "actual", label: "Bot actual" },
  { value: "nuevo", label: "Bot nuevo" },
];

const TABS: ReadonlyArray<{ id: Tab; label: string; icon: () => ReactNode }> = [
  { id: "resumen", label: "Resumen", icon: Icon.spark },
  { id: "conversaciones", label: "Conversaciones", icon: Icon.timeline },
  { id: "calibracion", label: "Calibración", icon: Icon.tag },
  { id: "legado", label: "Métricas legadas", icon: Icon.archive },
  { id: "goldens", label: "Goldens", icon: Icon.shield },
];

/**
 * Panel "Calidad LLM" del agente de ventas: el **scorecard por etapa**. Un
 * checklist binario por etapa del guion, con checks críticos que reprueban
 * solos, evaluado sobre la trayectoria completa (texto, tools, componentes,
 * etiquetas, guardas). Todas las superficies leen `/api/agents/evals/*`.
 *
 *   * **Resumen** — veredictos, Pareto de fallos, embudo de etapa terminal y
 *     tendencia semanal por check.
 *   * **Conversaciones** — matriz episodios × checks; elegir un episodio abre su
 *     tira de trayectoria + scorecard.
 *   * **Calibración** — confiabilidad del juez contra etiquetas humanas + cola.
 *   * **Métricas legadas** — la tendencia y los episodios del eval por promedio,
 *     intactos mientras conviven ambos sistemas.
 *   * **Goldens** — curación de candidatos.
 *
 * Nota FSD: composición intra-plugin (feature → feature del MISMO plugin), que
 * `dependency-cruiser` permite. El estado compartido entre superficies (filtros
 * de la matriz, episodio y check seleccionados, estado del legado) vive ACÁ,
 * lifted: las features hermanas no se hablan entre sí, reciben callbacks.
 */
export function AgentsQuality() {
  const [tab, setTab] = useState<Tab>("resumen");

  // Scorecard
  const [verdictFilter, setVerdictFilter] = useState<VerdictFilter>("todos");
  const [checkFilter, setCheckFilter] = useState<string | null>(null);
  const [selectedEpisode, setSelectedEpisode] = useState<EpisodeRef | null>(null);
  const [selectedCheckId, setSelectedCheckId] = useState<string | null>(null);

  // Legado (sin cambios de comportamiento)
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [selectedEpisodeKey, setSelectedEpisodeKey] = useState<string | null>(null);
  const [goldenToOpen, setGoldenToOpen] = useState<string | null>(null);
  const [onlyFailing, setOnlyFailing] = useState(false);

  // Alerta: episodios con veredicto FALLA (cayó al menos un check crítico).
  // Mismo query que la matriz (cache compartido).
  const [bot, setBot] = useState<ScorecardBot | null>(null);
  const { data: list } = useScorecards(WINDOW_DAYS, bot);
  const failingCount = (list?.scorecards ?? []).filter((s) => s.verdict === "FALLA").length;

  const goConversations = (verdict: VerdictFilter, check: string | null) => {
    setVerdictFilter(verdict);
    setCheckFilter(check);
    setTab("conversaciones");
  };

  const selectEpisode = (sessionId: string, episodeId: string) => {
    setSelectedEpisode({ sessionId, episodeId });
    // Viniendo del Pareto, abrir directamente en el check que se está investigando.
    setSelectedCheckId(checkFilter);
  };

  // Legado: día y episodio son filtros mutuamente excluyentes de la misma vista.
  const selectDate = (date: string | null) => {
    setSelectedDate(date);
    if (date) setSelectedEpisodeKey(null);
  };
  const selectLegacyEpisode = (key: string | null) => {
    setSelectedEpisodeKey(key);
    if (key) setSelectedDate(null);
  };
  const openCandidate = (candidateId: string) => {
    setGoldenToOpen(candidateId);
    setTab("goldens");
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden text-fg">
      <nav className="flex shrink-0 flex-wrap items-center gap-1 border-b border-line p-2">
        <div role="tablist" aria-label="Vistas de calidad LLM" className="flex flex-wrap items-center gap-1">
          {TABS.map((t) => {
            const TabIcon = t.icon;
            const on = tab === t.id;
            return (
              <button
                key={t.id}
                id={`quality-tab-${t.id}`}
                type="button"
                role="tab"
                aria-selected={on}
                aria-controls={`quality-panel-${t.id}`}
                onClick={() => setTab(t.id)}
                className={
                  "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition " +
                  (on ? "bg-white/10 text-fg" : "text-fg-muted hover:bg-white/5")
                }
              >
                <TabIcon /> {t.label}
              </button>
            );
          })}
        </div>
        <div role="radiogroup" aria-label="Bot que respondió" className="flex items-center gap-1">
          {BOT_OPTIONS.map((o) => (
            <label
              key={o.label}
              className={
                "cursor-pointer rounded-md px-2.5 py-1.5 text-xs font-medium transition " +
                (bot === o.value ? "bg-white/10 text-fg" : "text-fg-muted hover:bg-white/5")
              }
            >
              <input
                type="radio"
                name="quality-bot"
                className="sr-only"
                checked={bot === o.value}
                onChange={() => setBot(o.value)}
              />
              {o.label}
            </label>
          ))}
        </div>
        {failingCount > 0 && (
          <button
            type="button"
            onClick={() => goConversations("FALLA", null)}
            className="ml-auto inline-flex items-center gap-1.5 rounded-md bg-red/15 px-3 py-1.5 text-xs font-semibold text-red transition hover:bg-red/25"
            title={`Episodios de los últimos ${WINDOW_DAYS} días cuyo scorecard dio FALLA (cayó al menos un check crítico)`}
          >
            <Icon.alert /> {failingCount} episodio{failingCount > 1 ? "s" : ""} para revisar
          </button>
        )}
      </nav>

      <div
        role="tabpanel"
        id={`quality-panel-${tab}`}
        aria-labelledby={`quality-tab-${tab}`}
        className={
          tab === "legado" || tab === "goldens"
            ? "flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3"
            : "min-h-0 flex-1 overflow-y-auto p-3"
        }
      >
        {tab === "resumen" && (
          <SummaryView
            days={STATS_DAYS}
            bot={bot}
            onSelectVerdict={(v) => goConversations(v, null)}
            onSelectCheck={(id) => goConversations("todos", id)}
          />
        )}

        {tab === "conversaciones" && (
          <div className="flex min-w-0 flex-col gap-3">
            <ComplianceMatrix
              days={WINDOW_DAYS}
              bot={bot}
              verdictFilter={verdictFilter}
              onVerdictFilterChange={setVerdictFilter}
              checkFilter={checkFilter}
              onClearCheckFilter={() => setCheckFilter(null)}
              selectedEpisode={selectedEpisode}
              onSelectEpisode={selectEpisode}
            />
            {selectedEpisode ? (
              <ConversationDetail
                key={`${selectedEpisode.sessionId}::${selectedEpisode.episodeId}`}
                episode={selectedEpisode}
                selectedCheckId={selectedCheckId}
                onSelectCheck={setSelectedCheckId}
                onClose={() => setSelectedEpisode(null)}
              />
            ) : (
              <p className="rounded-lg border border-dashed border-line-strong p-4 text-sm text-fg-muted">
                Elige una conversación en la matriz para ver su trayectoria turno a turno y su scorecard.
              </p>
            )}
          </div>
        )}

        {tab === "calibracion" && <JudgeCalibration days={WINDOW_DAYS} />}

        {tab === "legado" && (
          <>
            <div className="shrink-0">
              <EvalTrendChart
                windowDays={LEGACY_WINDOW_DAYS}
                selectedDate={selectedDate}
                onSelectDate={selectDate}
                selectedEpisodeKey={selectedEpisodeKey}
                onSelectEpisode={selectLegacyEpisode}
              />
            </div>
            <div className="flex min-h-[20rem] flex-1 flex-col overflow-hidden rounded-lg border border-line">
              <EpisodeEvals
                windowDays={LEGACY_WINDOW_DAYS}
                dateFilter={selectedDate}
                onClearDateFilter={() => setSelectedDate(null)}
                selectedKey={selectedEpisodeKey}
                onSelectKey={selectLegacyEpisode}
                onlyFailing={onlyFailing}
                onOnlyFailingChange={setOnlyFailing}
                onOpenCandidate={openCandidate}
              />
            </div>
          </>
        )}

        {tab === "goldens" && (
          <div className="flex min-h-[20rem] flex-1 flex-col overflow-hidden rounded-lg border border-line">
            <GoldenEvalCuration initialSelectedId={goldenToOpen} />
          </div>
        )}
      </div>
    </div>
  );
}

export default AgentsQuality;
