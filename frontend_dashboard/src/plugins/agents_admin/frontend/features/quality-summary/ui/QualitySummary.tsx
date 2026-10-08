/**
 * Resumen de Calidad LLM con la vista del laboratorio, sobre producción
 * (decisión del operador, 2026-10-02), y la sección de cada bot. Desde el
 * 2026-10-08 (operador) los bots se llaman Botsito (el workflow actual, sin
 * Jev) y Colossus (el workflow nuevo, con Jev y el motor de decisiones).
 *
 * El Resumen compara a los dos, en este orden:
 *  1. cómo le fue a cada bot, en porcentajes, y el cumplimiento de cada etapa;
 *  2. dónde terminan los episodios: Botsito y, al lado, Colossus;
 *  3. el cumplimiento por check semana a semana, con su selector de bot;
 *  4. la tendencia de calidad (el eval por promedio), con su selector de bot.
 *
 * La sección de un bot trae las mismas gráficas solo con ese bot, la matriz
 * episodios × checks (una fila abre la conversación, una falla lleva al turno
 * que la tiene) y, a la derecha, su ficha técnica.
 */

import { useId, useMemo, useState, type ReactNode } from "react";

import { useCheckStats, type CheckStats } from "@plugins/agents_admin/frontend/entities/check-stats";
import { BOT_LABEL, useJevReport, type JevReport, type QualityBot } from "@plugins/agents_admin/frontend/entities/production-quality";
import { useCheckRegistry, useScorecards, type CheckDefinition } from "@plugins/agents_admin/frontend/entities/scorecard";
import { EvalTrendChart } from "@plugins/agents_admin/frontend/features/eval-trend-chart";
import {
  capabilityLabel,
  fallbackReasonLabel,
  filterMatrixRows,
  formatUsd,
  matrixFinalStages,
  matrixGroups,
  matrixRowKey,
  qualityPercent,
  qualityStageLabel,
  qualityStageRank,
  stageCompliance,
  toMatrixRowView,
  toQualityFunnel,
  type CheckInfoView,
  type MatrixVerdictFilter,
  type QualityVerdict,
} from "@/shared/lib";
import { CheckInfoDialog, CheckTrend, ComplianceMatrixLegend, ComplianceMatrixTable, StageFunnel } from "@/shared/ui";

import { BotSpecs } from "./BotSpecs";

interface MatrixProps {
  days: number;
  bot: QualityBot;
  /** Abre la pestaña Conversaciones con esa conversación elegida y, desde
   * una falla de la matriz, en el turno de ese check en ese episodio. */
  onOpenConversation: (sid: string, focus?: ConversationFocus) => void;
}

/** El turno al que lleva una falla de la matriz: el de ese check en ese episodio. */
export interface ConversationFocus {
  episodeId: string;
  checkId: string;
}

const CARD = "rounded-lg border border-line p-3";
const H3 = "m-0 text-sm font-semibold text-fg";
const NOTE = "m-0 text-[11.5px] text-fg-muted";

/** Ventana (días) de la tendencia de calidad: la de la vista legada, sin cambios. */
const TREND_WINDOW_DAYS = 30;


function pct(rate: number | null): string {
  return rate === null ? "—" : `${(rate * 100).toLocaleString("es-CO", { maximumFractionDigits: 1 })} %`;
}

function seconds(ms: number | null): string {
  return ms === null ? "—" : `${(ms / 1000).toLocaleString("es-CO", { maximumFractionDigits: 1 })} s`;
}

function emptyText(bot: QualityBot | null): string {
  return bot
    ? `Aún no hay episodios de ${BOT_LABEL[bot]} en esta ventana.`
    : "Aún no hay episodios calificados: se califican, turno por turno, al cerrar cada episodio.";
}

/** Una gráfica de `useCheckStats`, con su carga, su error y su vacío. */
function StatsBody({ days, bot, children }: { days: number; bot: QualityBot | null; children: (stats: CheckStats) => ReactNode }) {
  const stats = useCheckStats(days, bot);
  if (stats.isPending) return <p className={NOTE}>Cargando la gráfica…</p>;
  if (stats.isError) return <p className={NOTE}>No se pudo leer la gráfica.</p>;
  if (stats.data.episodes === 0) return <p className="m-0 rounded-lg border border-dashed border-line-strong p-4 text-sm text-fg-muted">{emptyText(bot)}</p>;
  return <>{children(stats.data)}</>;
}

// ── El selector de bot ───────────────────────────────────────────────────────

const BOT_CHOICES: ReadonlyArray<{ value: QualityBot | null; label: string }> = [
  { value: null, label: "Los dos" },
  { value: "actual", label: BOT_LABEL.actual },
  { value: "nuevo", label: BOT_LABEL.nuevo },
];

/** Botsito, Colossus o los dos: el filtro de una gráfica del Resumen o de Conversaciones. */
export function BotPicker({ label, value, onChange }: { label: string; value: QualityBot | null; onChange: (bot: QualityBot | null) => void }) {
  const name = useId();
  return (
    <div role="radiogroup" aria-label={label} className="flex items-center gap-1">
      {BOT_CHOICES.map((o) => (
        <label
          key={o.label}
          className={
            "cursor-pointer rounded-md px-2.5 py-1 text-xs font-medium transition " +
            (value === o.value ? "bg-white/10 text-fg" : "text-fg-muted hover:bg-white/5")
          }
        >
          <input type="radio" name={name} className="sr-only" checked={value === o.value} onChange={() => onChange(o.value)} />
          {o.label}
        </label>
      ))}
    </div>
  );
}

function whoseEpisodes(bot: QualityBot | null): string {
  return bot ? `Solo los episodios de ${BOT_LABEL[bot]}.` : "Los episodios de los dos bots.";
}

// ── Las gráficas ─────────────────────────────────────────────────────────────

const FUNNEL_NOTE = "Etapa final de cada episodio y su veredicto; al lado, cuántos terminaron ahí y qué parte del total son.";

/** Dónde terminan los episodios: de un bot o, en el Resumen, de cada uno lado a lado. */
function Funnels({ days, bots }: { days: number; bots: readonly QualityBot[] }) {
  const titleId = useId();
  const single = bots.length === 1;
  return (
    <section className={CARD} aria-labelledby={titleId}>
      <h3 id={titleId} className={H3}>Dónde terminan los episodios</h3>
      <p className="mb-2 mt-1 text-[11px] text-fg-faint">{FUNNEL_NOTE}</p>
      <div className={single ? "max-w-[760px]" : "grid items-start gap-4 min-[1100px]:grid-cols-2"}>
        {bots.map((bot) => (
          <div key={bot} className="min-w-0">
            {single ? null : <h4 className="m-0 mb-1.5 text-[12.5px] font-semibold text-fg">{BOT_LABEL[bot]}</h4>}
            <StatsBody days={days} bot={bot}>
              {(stats) => <StageFunnel funnel={toQualityFunnel(stats.funnel)} />}
            </StatsBody>
          </div>
        ))}
      </div>
    </section>
  );
}

/** El cumplimiento por check semana a semana: de un bot o, con `pickable`, con su selector. */
function Trend({ days, bot: fixed, pickable = false }: { days: number; bot: QualityBot | null; pickable?: boolean }) {
  const titleId = useId();
  const [picked, setPicked] = useState<QualityBot | null>(fixed);
  const bot = pickable ? picked : fixed;
  return (
    <section className={CARD} aria-labelledby={titleId}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 id={titleId} className={H3}>Cumplimiento por check, semana a semana</h3>
        {pickable ? <BotPicker label="Bot del cumplimiento por check" value={picked} onChange={setPicked} /> : null}
      </div>
      {pickable ? <p className={"mt-1 " + NOTE}>{whoseEpisodes(bot)}</p> : null}
      <StatsBody days={days} bot={bot}>
        {(stats) => <CheckTrend trend={stats.trend} />}
      </StatsBody>
    </section>
  );
}

/** La tendencia de calidad del eval por promedio (antes, «Métricas legadas»). */
function QualityTrend({ bot: fixed, pickable = false }: { bot: QualityBot | null; pickable?: boolean }) {
  const [picked, setPicked] = useState<QualityBot | null>(fixed);
  const bot = pickable ? picked : fixed;
  return (
    <EvalTrendChart
      key={bot ?? "todos"}
      windowDays={TREND_WINDOW_DAYS}
      bot={bot}
      controls={pickable ? <BotPicker label="Bot de la tendencia de calidad" value={picked} onChange={setPicked} /> : null}
    />
  );
}

function checkInfo(c: CheckDefinition): CheckInfoView {
  return { id: c.id, name: c.name || c.id, level: c.level, kind: c.kind, family: c.family_label, stage: c.stage, applies: c.applies, rule: c.rule };
}

// ── La matriz ────────────────────────────────────────────────────────────────

const VERDICT_OPTIONS: ReadonlyArray<{ value: MatrixVerdictFilter; label: string }> = [
  { value: "todos", label: "Todos" },
  { value: "FALLA", label: "Falla" },
  { value: "ALERTA", label: "Alerta" },
  { value: "PASA", label: "Pasa" },
];

const ROW_CAP = 120;

function Matrix({ days, bot, onOpenConversation }: MatrixProps) {
  const cards = useScorecards(days, bot);
  const registry = useCheckRegistry();
  const [verdict, setVerdict] = useState<MatrixVerdictFilter>("todos");
  const [stage, setStage] = useState<string | null>(null);
  const [onlyFailing, setOnlyFailing] = useState(false);
  const all = useMemo(() => cards.data?.scorecards ?? [], [cards.data]);
  const rows = useMemo(() => filterMatrixRows(all, { verdict, stage }), [all, verdict, stage]);
  const groups = useMemo(() => matrixGroups(registry.data?.checks ?? [], rows, { onlyFailing }), [registry.data, rows, onlyFailing]);
  const stages = useMemo(() => matrixFinalStages(all), [all]);
  const byKey = useMemo(() => new Map(rows.map((r) => [matrixRowKey(r), r])), [rows]);
  const [explained, setExplained] = useState<string | null>(null);
  const explainedCheck = registry.data?.checks.find((c) => c.id === explained);

  let body: ReactNode;
  if (cards.isPending) body = <p className="text-sm text-fg-muted">Cargando la matriz…</p>;
  else if (cards.isError) body = <p className="text-sm text-fg-muted">No se pudo leer la matriz.</p>;
  else if (all.length === 0) body = <p className="text-sm text-fg-muted">{emptyText(bot)}</p>;
  else if (rows.length === 0) body = <p className="text-sm text-fg-muted">Ningún episodio coincide con los filtros.</p>;
  else {
    body = (
      <ComplianceMatrixTable
        groups={groups}
        rows={rows.map(toMatrixRowView)}
        selectedKey={null}
        onSelectRow={(view) => {
          const row = byKey.get(view.key);
          if (row) onOpenConversation(row.session_id);
        }}
        onSelectColumn={setExplained}
        onSelectFailure={(view, checkId) => {
          const row = byKey.get(view.key);
          if (row) onOpenConversation(row.session_id, { episodeId: row.episode_id, checkId });
        }}
        rowCap={ROW_CAP}
        resetKey={`${bot}|${verdict}|${stage ?? ""}`}
      />
    );
  }

  return (
    <section className={CARD + " flex min-w-0 flex-col gap-2"} aria-label="Matriz de cumplimiento por episodio">
      <h3 className={H3}>Cada episodio, check por check</h3>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <div role="group" aria-label="Filtrar por veredicto" className="flex items-center gap-1">
          <span className="mr-1 text-[10px] font-semibold uppercase tracking-wider text-fg-faint">Veredicto</span>
          {VERDICT_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              aria-pressed={verdict === o.value}
              onClick={() => setVerdict(o.value)}
              className={"rounded-full border px-2.5 py-0.5 transition " + (verdict === o.value ? "border-fg bg-fg" : "border-line-strong hover:bg-white/5")}
            >
              {/* El color va en el span: `button { color: inherit }` de index.css le gana a las utilidades. */}
              <span className={verdict === o.value ? "text-win-bg" : "text-fg"}>{o.label}</span>
            </button>
          ))}
        </div>
        <label className="ml-2 inline-flex items-center gap-1.5 text-fg-muted">
          Etapa final
          <select
            value={stage ?? ""}
            onChange={(e) => setStage(e.target.value || null)}
            className="rounded-md border border-line-strong bg-canvas px-1.5 py-0.5 text-xs text-fg"
          >
            <option value="">Todas</option>
            {stages.map((s) => (
              <option key={s} value={s}>
                {qualityStageLabel(s)}
              </option>
            ))}
          </select>
        </label>
        <label className="ml-2 inline-flex items-center gap-1.5 text-fg-muted">
          <input type="checkbox" checked={onlyFailing} onChange={(e) => setOnlyFailing(e.target.checked)} className="accent-accent" />
          Solo checks con fallas
        </label>
        <span className="ml-auto text-fg-faint">{`${rows.length} de ${all.length} episodios`}</span>
      </div>
      {body}
      <ComplianceMatrixLegend hint="Elige una fila para abrir la conversación, una ✗ para ir al turno que falló o el código de un check para ver qué califica." />
      {explainedCheck ? <CheckInfoDialog check={checkInfo(explainedCheck)} onClose={() => setExplained(null)} /> : null}
    </section>
  );
}

// ── Cómo le fue a cada bot ───────────────────────────────────────────────────

const VERDICT_COLUMNS: ReadonlyArray<[QualityVerdict, string, string]> = [
  ["PASA", "Pasan", "text-ok"],
  ["ALERTA", "En alerta", "text-warn"],
  ["FALLA", "Fallan", "text-danger"],
  ["SIN_DATOS", "Sin datos", "text-fg-muted"],
];
const BOTS: readonly QualityBot[] = ["actual", "nuevo"];

/** Cómo le fue a cada bot de `bots`: en el Resumen, a los dos; en la sección de un bot, solo a ese. */
function ResultsByBot({ days, bots }: { days: number; bots: readonly QualityBot[] }) {
  const titleId = useId();
  // Siempre los dos hooks (reglas de hooks): el que no se muestra sale del caché.
  const stats: Record<QualityBot, CheckStats | undefined> = {
    actual: useCheckStats(days, "actual").data,
    nuevo: useCheckStats(days, "nuevo").data,
  };
  const registry = useCheckRegistry();
  const stageOf = useMemo(() => {
    const map = new Map((registry.data?.checks ?? []).map((c) => [c.id, c.stage]));
    return (id: string) => map.get(id);
  }, [registry.data]);
  // Una fila por etapa con datos en algún bot, en el orden del guion.
  const perBot = new Map(bots.map((b) => [b, new Map(stageCompliance(stats[b]?.trend ?? [], stageOf).map((r) => [r.stage, r]))]));
  const stageRows = [...new Map([...perBot.values()].flatMap((m) => [...m.values()]).map((r) => [r.stage, r])).values()].sort(
    (a, b) => qualityStageRank(a.stage) - qualityStageRank(b.stage),
  );

  return (
    <section className={CARD} aria-labelledby={titleId}>
      <h3 id={titleId} className={H3}>{bots.length === 1 ? `Cómo le fue a ${BOT_LABEL[bots[0]]}` : "Cómo le fue a cada bot"}</h3>
      {/* Con un solo bot la sección comparte el ancho con la ficha técnica: las tablas van una debajo de la otra. */}
      <div className={"mt-2 grid items-start gap-4 " + (bots.length > 1 ? "min-[1500px]:grid-cols-2" : "min-[1800px]:grid-cols-2")}>
        <div className="overflow-x-auto">
          <table aria-label="Resultado por bot" className="w-full border-collapse text-[12.5px] tabular-nums">
            <thead>
              <tr className="text-left text-[11px] text-fg-faint">
                {["Bot", "Episodios", ...VERDICT_COLUMNS.map(([, label]) => label)].map((h) => (
                  <th key={h} className="py-1 pr-3 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bots.map((bot) => {
                const s = stats[bot];
                return (
                  <tr key={bot} className="border-t border-line">
                    <th scope="row" className="py-1.5 pr-3 text-left font-medium text-fg">{BOT_LABEL[bot]}</th>
                    <td className="py-1.5 pr-3">{s ? s.episodes : "…"}</td>
                    {VERDICT_COLUMNS.map(([v, , tone]) => (
                      <td key={v} className={"py-1.5 pr-3 " + tone}>
                        {s ? (
                          <>
                            <span className="font-semibold">{qualityPercent(s.verdicts[v], s.episodes)}</span>
                            <span className="ml-1 text-[11px] text-fg-faint">{`(${s.verdicts[v]})`}</span>
                          </>
                        ) : null}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className={"mt-2 " + NOTE}>
            Pasa: cumplió todo lo importante · En alerta: falló algo importante (un check mayor) · Falla: falló algo crítico.
            {bots.length > 1 ? " Son conversaciones distintas: cada una la atendió un solo bot." : ""}
          </p>
        </div>
        <div className="overflow-x-auto">
          <table aria-label="Cumplimiento por etapa" className="w-full border-collapse text-[12.5px] tabular-nums">
            <thead>
              <tr className="text-left text-[11px] text-fg-faint">
                <th className="py-1 pr-3 font-medium">Etapa</th>
                {bots.map((bot) => (
                  <th key={bot} className="py-1 pr-3 font-medium">{BOT_LABEL[bot]}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {stageRows.map((row) => (
                <tr key={row.stage} className="border-t border-line">
                  <th scope="row" className="py-1.5 pr-3 text-left font-medium text-fg">
                    <span className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm align-middle" style={{ background: row.color }} aria-hidden="true" />
                    {row.label}
                  </th>
                  {bots.map((bot) => {
                    const cell = perBot.get(bot)?.get(row.stage);
                    return (
                      <td key={bot} className="py-1.5 pr-3 text-fg">
                        {cell ? (
                          <>
                            <span className="font-semibold">{qualityPercent(cell.passed, cell.applicable)}</span>
                            <span className="ml-1 text-[11px] text-fg-faint">{`(${cell.passed} de ${cell.applicable})`}</span>
                          </>
                        ) : (
                          <span className="text-fg-faint">—</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <p className={"mt-2 " + NOTE}>
            De las veces que un check de la etapa aplicó, cuántas pasó.
          </p>
        </div>
      </div>
    </section>
  );
}

// ── Jev en producción ────────────────────────────────────────────────────────

function failedReasons(report: JevReport): string {
  const d = report.decisions;
  if (!d) return "";
  const reasons = Object.entries(d.jev_failed_by_reason)
    .sort((a, b) => b[1] - a[1])
    .map(([reason, n]) => `${fallbackReasonLabel(reason)}: ${n}`);
  const capabilities = Object.entries(d.jev_failed_by_capability)
    .sort((a, b) => b[1] - a[1])
    .map(([cap, n]) => `${capabilityLabel(cap)}: ${n}`);
  return [reasons.join(", "), capabilities.length ? `capacidades: ${capabilities.join(", ")}` : ""].filter(Boolean).join(" · ");
}

function JevInProduction({ days }: { days: number }) {
  const report = useJevReport(days, "nuevo");
  let body: ReactNode;
  if (report.isPending) body = <p className={NOTE}>Cargando lo que hizo Jev…</p>;
  else if (report.isError) body = <p className={NOTE}>No se pudo leer el informe de Jev.</p>;
  else if (report.data.turns === 0) body = <p className={NOTE}>Todavía no hay conversaciones de Colossus en esta ventana.</p>;
  else {
    const r = report.data;
    const d = r.decisions;
    const rows: Array<[string, string]> = [
      ["Turnos que respondió", `${r.turns} turnos en ${r.episodes} ${r.episodes === 1 ? "episodio" : "episodios"}.`],
      [
        "Tiempo de respuesta",
        r.perception?.p95_ms != null
          ? `El 95 % de las respuestas de Jev llegó en menos de ${seconds(r.perception.p95_ms)} (la mitad, en menos de ${seconds(r.perception.p50_ms)}).`
          : "—",
      ],
      ["No respondió a tiempo o falló", r.perception ? `${pct(r.perception.fallback_rate)} de las preguntas: en esas decidió la regla de hoy.` : "—"],
      [
        "Decisiones de las capacidades",
        d
          ? `${d.jev_failed} de ${d.asked_jev} decisiones cayeron a la regla porque Jev falló${d.jev_failed ? ` (${failedReasons(r)})` : ""}.`
          : "—",
      ],
      [
        "Costo por turno",
        r.cost_per_turn_usd !== null
          ? `${formatUsd(r.cost_per_turn_usd, 4)}${r.perception_cost_per_turn_usd !== null ? ` (Jev: ${formatUsd(r.perception_cost_per_turn_usd, 4)})` : ""}`
          : "—",
      ],
      ["Mensaje de complemento", `${pct(r.complement_rate)} de los turnos (cuando la respuesta dejó un asunto sin cubrir).`],
      ["Segunda vuelta del modelo", `${pct(r.extra_round_rate)} de los turnos.`],
    ];
    body = (
      <dl className="m-0 mt-2 grid grid-cols-1 gap-x-4 gap-y-1 text-[12.5px] min-[640px]:grid-cols-[max-content_1fr]">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-fg-muted">{k}</dt>
            <dd className="m-0 text-fg-soft">{v}</dd>
          </div>
        ))}
      </dl>
    );
  }
  return (
    <section aria-label="Jev en producción" className={CARD}>
      <h3 className={H3}>Jev en producción</h3>
      <p className={"mt-1 " + NOTE}>Los turnos reales de Colossus: cómo respondió Jev, cuándo decidió la regla de hoy y cuánto costó.</p>
      {body}
    </section>
  );
}

// ── El Resumen ───────────────────────────────────────────────────────────────

/** El Resumen: los dos bots, comparados (operador, 2026-10-08). */
export function QualitySummary({ days }: { days: number }) {
  return (
    <div className="flex flex-col gap-3">
      <ResultsByBot days={days} bots={BOTS} />
      <Funnels days={days} bots={BOTS} />
      <Trend days={days} bot={null} pickable />
      <QualityTrend bot={null} pickable />
    </div>
  );
}

// ── La sección de un bot ─────────────────────────────────────────────────────

/** La sección de Botsito o de Colossus: sus gráficas, ya filtradas, y a la
 *  derecha su ficha técnica. */
export function BotQuality({
  days,
  bot,
  onOpenConversation,
  onOpenEngine,
}: MatrixProps & {
  /** Abre la pestaña «Motor de decisiones» (desde la ficha de Colossus). */
  onOpenEngine: () => void;
}) {
  const cards = useScorecards(days, bot);
  // Si la API todavía no filtra, ignora `?bot=` y devuelve todos: se avisa.
  const serverIgnoredBot = cards.data !== undefined && cards.data.bot !== bot;
  const only: readonly QualityBot[] = [bot];
  return (
    <div className="grid items-start gap-3 min-[1200px]:grid-cols-[minmax(0,1fr)_minmax(300px,360px)]">
      <div className="flex min-w-0 flex-col gap-3">
        {serverIgnoredBot ? (
          <p role="status" className="m-0 text-xs text-yellow">
            El servidor no filtró por bot: lo que ves son todos los episodios.
          </p>
        ) : null}
        <ResultsByBot days={days} bots={only} />
        <Funnels days={days} bots={only} />
        <Trend days={days} bot={bot} />
        <QualityTrend bot={bot} />
        {bot === "nuevo" ? <JevInProduction days={days} /> : null}
        <Matrix days={days} bot={bot} onOpenConversation={onOpenConversation} />
      </div>
      <BotSpecs bot={bot} onOpenEngine={onOpenEngine} />
    </div>
  );
}
