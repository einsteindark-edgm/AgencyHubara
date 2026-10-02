import { useState, type ReactNode } from "react";

import { BOT_LABEL, type QualityBot } from "@plugins/agents_admin/frontend/entities/production-quality";
import { useScorecards } from "@plugins/agents_admin/frontend/entities/scorecard";
import { DecisionEngineView } from "@plugins/agents_admin/frontend/features/decision-engine";
import { EpisodeEvals } from "@plugins/agents_admin/frontend/features/episode-evals";
import { EvalTrendChart } from "@plugins/agents_admin/frontend/features/eval-trend-chart";
import { GoldenEvalCuration } from "@plugins/agents_admin/frontend/features/golden-eval-curation";
import { JudgeCalibration } from "@plugins/agents_admin/frontend/features/judge-calibration";
import { QualityConversations } from "@plugins/agents_admin/frontend/features/quality-conversations";
import { QualitySummary } from "@plugins/agents_admin/frontend/features/quality-summary";
import type { QualityVerdict } from "@/shared/lib";
import { Icon } from "@/shared/ui";

/** Ventana (días) del scorecard: la MISMA para la alerta, la matriz y los
 *  agregados (8 semanas: la tendencia semanal necesita historia). Una sola
 *  ventana evita que el resumen diga "10 en FALLA" y la matriz muestre 7. */
const WINDOW_DAYS = 56;
const STATS_DAYS = WINDOW_DAYS;
/** Ventana de las métricas legadas (sin cambios respecto de la vista anterior). */
const LEGACY_WINDOW_DAYS = 30;

type Tab = "resumen" | "conversaciones" | "motor" | "calibracion" | "legado" | "goldens";

/** Las mismas vistas separan los episodios de cada bot: el actual o el bot
 *  Jev (el workflow nuevo, decisión del operador del 2026-10-02). */
const BOT_OPTIONS: ReadonlyArray<{ value: QualityBot | null; label: string }> = [
  { value: null, label: "Todos" },
  { value: "actual", label: BOT_LABEL.actual },
  { value: "nuevo", label: BOT_LABEL.nuevo },
];

const TABS: ReadonlyArray<{ id: Tab; label: string; icon: () => ReactNode }> = [
  { id: "resumen", label: "Resumen", icon: Icon.spark },
  { id: "conversaciones", label: "Conversaciones", icon: Icon.timeline },
  { id: "motor", label: "Motor de decisiones", icon: Icon.wand },
  { id: "calibracion", label: "Calibración", icon: Icon.tag },
  { id: "legado", label: "Métricas legadas", icon: Icon.archive },
  { id: "goldens", label: "Goldens", icon: Icon.shield },
];

/**
 * Panel "Calidad LLM" del agente de ventas, con la vista del laboratorio
 * sobre producción (decisión del operador, 2026-10-02: «eliminar la forma
 * actual de ver la calificación y reemplazarla por la del laboratorio»). Cada
 * episodio real se califica turno por turno, como el laboratorio califica su
 * bot de producción. Todas las superficies leen `/api/agents/evals/*`.
 *
 *   * **Resumen** — cumplimiento por check semana a semana, dónde terminan
 *     los episodios y la matriz episodios × checks; a pedido, cómo le fue a
 *     cada bot y cómo anduvo Jev en los turnos reales.
 *   * **Conversaciones** — cada conversación real como un hilo con cada turno
 *     calificado; la ventana del turno trae el resultado, el paso a paso y
 *     las decisiones de Jev.
 *   * **Motor de decisiones** — la versión del motor que corre la tienda y
 *     cada decisión que toma, por la parte del software donde actúa, con lo
 *     que resuelve y quién la decide hoy.
 *   * **Calibración** — confiabilidad del juez contra etiquetas humanas + cola.
 *   * **Métricas legadas** — la tendencia y los episodios del eval por promedio.
 *   * **Goldens** — curación de candidatos.
 *
 * Nota FSD: composición intra-plugin (feature → feature del MISMO plugin), que
 * `dependency-cruiser` permite. El estado compartido entre superficies (bot,
 * conversación elegida, filtro de la alerta) vive ACÁ, lifted: las features
 * hermanas no se hablan entre sí, reciben callbacks.
 */
export function AgentsQuality() {
  const [tab, setTab] = useState<Tab>("resumen");

  // Conversaciones: la elegida desde el Resumen y el filtro de la alerta.
  const [openSid, setOpenSid] = useState<string | null>(null);
  const [verdictFilter, setVerdictFilter] = useState<QualityVerdict | null>(null);

  // Legado (sin cambios de comportamiento)
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [selectedEpisodeKey, setSelectedEpisodeKey] = useState<string | null>(null);
  const [goldenToOpen, setGoldenToOpen] = useState<string | null>(null);
  const [onlyFailing, setOnlyFailing] = useState(false);

  // Alerta: episodios con veredicto FALLA (cayó al menos un check crítico).
  // Mismo query que la matriz del Resumen (cache compartido).
  const [bot, setBot] = useState<QualityBot | null>(null);
  const { data: list } = useScorecards(WINDOW_DAYS, bot);
  const failingCount = (list?.scorecards ?? []).filter((s) => s.verdict === "FALLA").length;
  // El filtro de bot solo aplica a Resumen y Conversaciones. Si la API todavía
  // no lo soporta, ignora `?bot=` y devuelve todos: se avisa.
  const botFilterShown = tab === "resumen" || tab === "conversaciones";
  const serverIgnoredBot = bot !== null && list !== undefined && list.bot !== bot;
  const chooseBot = (value: QualityBot | null) => {
    setBot(value);
    // La conversación abierta puede no ser de ese bot.
    setOpenSid(null);
  };

  const openConversation = (sid: string) => {
    setOpenSid(sid);
    setVerdictFilter(null);
    setTab("conversaciones");
  };
  const showFailing = () => {
    setOpenSid(null);
    setVerdictFilter("FALLA");
    setTab("conversaciones");
  };
  const chooseTab = (next: Tab) => {
    // Volver a Conversaciones desde la barra muestra todas.
    if (next === "conversaciones") setVerdictFilter(null);
    setTab(next);
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
                onClick={() => chooseTab(t.id)}
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
        {botFilterShown && (
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
                  onChange={() => chooseBot(o.value)}
                />
                {o.label}
              </label>
            ))}
          </div>
        )}
        {botFilterShown && serverIgnoredBot && (
          <p role="status" className="text-xs text-yellow">
            El servidor no filtró por bot: lo que ves son todos los episodios.
          </p>
        )}
        {failingCount > 0 && (
          <button
            type="button"
            onClick={showFailing}
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
        {tab === "resumen" && <QualitySummary days={STATS_DAYS} bot={bot} onOpenConversation={openConversation} />}

        {tab === "conversaciones" && (
          <QualityConversations
            key={`${bot ?? "todos"}|${verdictFilter ?? ""}|${openSid ?? ""}`}
            days={WINDOW_DAYS}
            bot={bot}
            verdictFilter={verdictFilter}
            initialSid={openSid}
          />
        )}

        {tab === "motor" && <DecisionEngineView />}

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
