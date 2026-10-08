import { useState, type ReactNode } from "react";

import { useQualityConversations, type QualityBot } from "@plugins/agents_admin/frontend/entities/production-quality";
import { useScorecards } from "@plugins/agents_admin/frontend/entities/scorecard";
import { DecisionEngineView } from "@plugins/agents_admin/frontend/features/decision-engine";
import { GoldenEvalCuration } from "@plugins/agents_admin/frontend/features/golden-eval-curation";
import { JudgeCalibration } from "@plugins/agents_admin/frontend/features/judge-calibration";
import { QualityConversations } from "@plugins/agents_admin/frontend/features/quality-conversations";
import {
  BotPicker,
  BotQuality,
  QualitySummary,
  type ConversationFocus,
} from "@plugins/agents_admin/frontend/features/quality-summary";
import type { QualityVerdict } from "@/shared/lib";
import { Icon } from "@/shared/ui";

/** Ventana (días) del scorecard: la MISMA para la alerta, la matriz y los
 *  agregados (8 semanas: la tendencia semanal necesita historia). Una sola
 *  ventana evita que el resumen diga "10 en FALLA" y la matriz muestre 7. */
const WINDOW_DAYS = 56;
const STATS_DAYS = WINDOW_DAYS;

type Tab = "resumen" | "botsito" | "colossus" | "conversaciones" | "motor" | "calibracion" | "goldens";

/** La sección propia de cada bot (operador, 2026-10-08). */
const BOT_OF_TAB: Partial<Record<Tab, QualityBot>> = { botsito: "actual", colossus: "nuevo" };

const TABS: ReadonlyArray<{ id: Tab; label: string; icon: () => ReactNode }> = [
  { id: "resumen", label: "Resumen", icon: Icon.spark },
  { id: "botsito", label: "Botsito", icon: Icon.bot },
  { id: "colossus", label: "Colossus", icon: Icon.bolt },
  { id: "conversaciones", label: "Conversaciones", icon: Icon.timeline },
  { id: "motor", label: "Motor de decisiones", icon: Icon.wand },
  { id: "calibracion", label: "Calibración", icon: Icon.tag },
  { id: "goldens", label: "Goldens", icon: Icon.shield },
];

/**
 * Panel "Calidad LLM" del agente de ventas, con la vista del laboratorio
 * sobre producción (decisión del operador, 2026-10-02: «eliminar la forma
 * actual de ver la calificación y reemplazarla por la del laboratorio»). Cada
 * episodio real se califica turno por turno, como el laboratorio califica su
 * bot de producción. Todas las superficies leen `/api/agents/evals/*`.
 *
 * Desde el 2026-10-08 (operador) cada bot es una sección propia en vez de un
 * filtro de las demás: Botsito (el workflow actual, sin Jev) y Colossus (el
 * workflow nuevo, con Jev y el motor de decisiones).
 *
 *   * **Resumen** — los dos bots comparados: cómo le fue a cada uno, dónde
 *     terminan sus episodios (lado a lado), el cumplimiento por check semana a
 *     semana y la tendencia de calidad, estas dos con su selector de bot.
 *   * **Botsito / Colossus** — las mismas gráficas solo con ese bot, la matriz
 *     episodios × checks y, a la derecha, su ficha técnica (Colossus: la
 *     versión del motor de decisiones y cómo se versiona; también cómo anduvo
 *     Jev en los turnos reales).
 *   * **Conversaciones** — cada conversación real como un hilo con cada turno
 *     calificado, con su propio filtro por bot; la ventana del turno trae el
 *     resultado, el paso a paso y las decisiones de Jev.
 *   * **Motor de decisiones** — la versión del motor que corre la tienda y
 *     cada decisión que toma, por la parte del software donde actúa, con lo
 *     que resuelve y quién la decide hoy.
 *   * **Calibración** — confiabilidad del juez contra etiquetas humanas + cola.
 *   * **Goldens** — curación de candidatos.
 *
 * Nota FSD: composición intra-plugin (feature → feature del MISMO plugin), que
 * `dependency-cruiser` permite. El estado compartido entre superficies (bot
 * de Conversaciones, conversación elegida, filtro de la alerta) vive ACÁ,
 * lifted: las features hermanas no se hablan entre sí, reciben callbacks.
 */
export function AgentsQuality() {
  const [tab, setTab] = useState<Tab>("resumen");

  // Conversaciones: su filtro por bot, la elegida desde la matriz de un bot y el filtro de la alerta.
  const [conversationsBot, setConversationsBot] = useState<QualityBot | null>(null);
  const [openSid, setOpenSid] = useState<string | null>(null);
  // Desde una falla de la matriz: el turno de ese check en ese episodio.
  const [openFocus, setOpenFocus] = useState<ConversationFocus | null>(null);
  const [verdictFilter, setVerdictFilter] = useState<QualityVerdict | null>(null);

  // Alerta: episodios con veredicto FALLA (cayó al menos un check crítico), de los dos bots.
  const { data: list } = useScorecards(WINDOW_DAYS, null);
  const failingCount = (list?.scorecards ?? []).filter((s) => s.verdict === "FALLA").length;
  // Si la API todavía no filtra Conversaciones, ignora `?bot=` y devuelve todas: se avisa.
  const { data: conversations } = useQualityConversations(WINDOW_DAYS, conversationsBot);
  const serverIgnoredBot = conversationsBot !== null && conversations !== undefined && conversations.bot !== conversationsBot;

  const chooseConversationsBot = (value: QualityBot | null) => {
    setConversationsBot(value);
    // La conversación abierta puede no ser de ese bot.
    setOpenSid(null);
    setOpenFocus(null);
  };
  const openConversation = (bot: QualityBot) => (sid: string, focus?: ConversationFocus) => {
    setConversationsBot(bot);
    setOpenSid(sid);
    setOpenFocus(focus ?? null);
    setVerdictFilter(null);
    setTab("conversaciones");
  };
  const showFailing = () => {
    setConversationsBot(null);
    setOpenSid(null);
    setOpenFocus(null);
    setVerdictFilter("FALLA");
    setTab("conversaciones");
  };
  const chooseTab = (next: Tab) => {
    // Volver a Conversaciones desde la barra muestra todas.
    if (next === "conversaciones") setVerdictFilter(null);
    setTab(next);
  };

  const sectionBot = BOT_OF_TAB[tab];

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

      {/* Una por pestaña: cada vista abre arriba, no en el scroll de la anterior. */}
      <div
        key={tab}
        role="tabpanel"
        id={`quality-panel-${tab}`}
        aria-labelledby={`quality-tab-${tab}`}
        className={
          tab === "goldens" ? "flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3" : "min-h-0 flex-1 overflow-y-auto p-3"
        }
      >
        {tab === "resumen" && <QualitySummary days={STATS_DAYS} />}

        {sectionBot && (
          <BotQuality
            key={sectionBot}
            days={STATS_DAYS}
            bot={sectionBot}
            onOpenConversation={openConversation(sectionBot)}
            onOpenEngine={() => setTab("motor")}
          />
        )}

        {tab === "conversaciones" && (
          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <BotPicker label="Bot de las conversaciones" value={conversationsBot} onChange={chooseConversationsBot} />
              {serverIgnoredBot && (
                <p role="status" className="m-0 text-xs text-yellow">
                  El servidor no filtró por bot: lo que ves son todas las conversaciones.
                </p>
              )}
            </div>
            <QualityConversations
              key={`${conversationsBot ?? "todos"}|${verdictFilter ?? ""}|${openSid ?? ""}|${openFocus ? `${openFocus.episodeId}:${openFocus.checkId}` : ""}`}
              days={WINDOW_DAYS}
              bot={conversationsBot}
              verdictFilter={verdictFilter}
              initialSid={openSid}
              initialFocus={openFocus}
            />
          </div>
        )}

        {tab === "motor" && <DecisionEngineView />}

        {tab === "calibracion" && <JudgeCalibration days={WINDOW_DAYS} />}

        {tab === "goldens" && (
          <div className="flex min-h-[20rem] flex-1 flex-col overflow-hidden rounded-lg border border-line">
            <GoldenEvalCuration />
          </div>
        )}
      </div>
    </div>
  );
}

export default AgentsQuality;
