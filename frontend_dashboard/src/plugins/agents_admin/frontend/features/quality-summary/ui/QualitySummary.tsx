/**
 * Resumen de Calidad LLM con la vista del laboratorio, sobre producción
 * (decisión del operador, 2026-10-02). En el orden que pidió el operador
 * (2026-10-07):
 *  1. la matriz episodios × checks con sus filtros, del bot elegido arriba:
 *     una fila abre esa conversación, el código de un check explica qué
 *     califica y una falla lleva al turno que la tiene;
 *  2. cómo le fue a cada bot, en porcentajes, y el cumplimiento de cada etapa;
 *  3. dónde terminan los episodios, con su porcentaje;
 *  4. el cumplimiento por check semana a semana.
 * Al final, a pedido, cómo anduvo Jev en los turnos reales.
 */

import { useMemo, useState, type ReactNode } from "react";

import { useCheckStats, type CheckStats } from "@plugins/agents_admin/frontend/entities/check-stats";
import { BOT_LABEL, useJevReport, type JevReport, type QualityBot } from "@plugins/agents_admin/frontend/entities/production-quality";
import { useCheckRegistry, useScorecards, type CheckDefinition } from "@plugins/agents_admin/frontend/entities/scorecard";
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

interface Props {
  days: number;
  bot: QualityBot | null;
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


function pct(rate: number | null): string {
  return rate === null ? "—" : `${(rate * 100).toLocaleString("es-CO", { maximumFractionDigits: 1 })} %`;
}

function seconds(ms: number | null): string {
  return ms === null ? "—" : `${(ms / 1000).toLocaleString("es-CO", { maximumFractionDigits: 1 })} s`;
}

const OF_BOT: Record<QualityBot, string> = { actual: "del bot actual", nuevo: "del bot Jev" };

function emptyText(bot: QualityBot | null): string {
  return bot
    ? `Aún no hay episodios ${OF_BOT[bot]} en esta ventana.`
    : "Aún no hay episodios calificados: se califican, turno por turno, al cerrar cada episodio.";
}

// ── Las dos gráficas ─────────────────────────────────────────────────────────

function Funnel({ stats }: { stats: CheckStats }) {
  // El embudo es un SVG que escala con el ancho: a lo ancho de la ventana se ve gigante.
  return (
    <section className={CARD} aria-labelledby="quality-funnel-title">
      <h3 id="quality-funnel-title" className={H3}>Dónde terminan los episodios</h3>
      <p className="mb-2 mt-1 text-[11px] text-fg-faint">
        Etapa final de cada episodio y su veredicto; al lado, cuántos terminaron ahí y qué parte del total son.
      </p>
      <div className="max-w-[760px]">
        <StageFunnel funnel={toQualityFunnel(stats.funnel)} />
      </div>
    </section>
  );
}

function Trend({ stats }: { stats: CheckStats }) {
  return (
    <section className={CARD} aria-labelledby="quality-trend-title">
      <h3 id="quality-trend-title" className={H3}>Cumplimiento por check, semana a semana</h3>
      <CheckTrend trend={stats.trend} />
    </section>
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

function Matrix({ days, bot, onOpenConversation }: Props) {
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
        resetKey={`${bot ?? "todos"}|${verdict}|${stage ?? ""}`}
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

function ResultsByBot({ days }: { days: number }) {
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
  const perBot = new Map(BOTS.map((b) => [b, new Map(stageCompliance(stats[b]?.trend ?? [], stageOf).map((r) => [r.stage, r]))]));
  const stageRows = [...new Map([...perBot.values()].flatMap((m) => [...m.values()]).map((r) => [r.stage, r])).values()].sort(
    (a, b) => qualityStageRank(a.stage) - qualityStageRank(b.stage),
  );

  return (
    <section className={CARD} aria-labelledby="quality-results-title">
      <h3 id="quality-results-title" className={H3}>Cómo le fue a cada bot</h3>
      <div className="mt-2 grid items-start gap-4 min-[1500px]:grid-cols-2">
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
              {BOTS.map((bot) => {
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
            Pasa: cumplió todo lo importante · En alerta: falló algo importante (un check mayor) · Falla: falló algo crítico. Son conversaciones distintas:
            cada una la atendió un solo bot.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table aria-label="Cumplimiento por etapa" className="w-full border-collapse text-[12.5px] tabular-nums">
            <thead>
              <tr className="text-left text-[11px] text-fg-faint">
                <th className="py-1 pr-3 font-medium">Etapa</th>
                {BOTS.map((bot) => (
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
                  {BOTS.map((bot) => {
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
  else if (report.data.turns === 0) body = <p className={NOTE}>Todavía no hay conversaciones del bot Jev en esta ventana.</p>;
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
      <p className={"mt-1 " + NOTE}>Los turnos reales del bot Jev: cómo respondió Jev, cuándo decidió la regla de hoy y cuánto costó.</p>
      {body}
    </section>
  );
}

// ── La pestaña ───────────────────────────────────────────────────────────────

export function QualitySummary({ days, bot, onOpenConversation }: Props) {
  const stats = useCheckStats(days, bot);
  const [jevOpen, setJevOpen] = useState(false);

  let charts: ReactNode;
  if (stats.isPending) charts = <p className="text-sm text-fg-muted">Cargando las gráficas…</p>;
  else if (stats.isError) charts = <p className="text-sm text-fg-muted">No se pudieron leer las gráficas.</p>;
  else if (stats.data.episodes === 0) charts = <p className="rounded-lg border border-dashed border-line-strong p-4 text-sm text-fg-muted">{emptyText(bot)}</p>;
  else {
    charts = (
      <>
        <Funnel stats={stats.data} />
        <Trend stats={stats.data} />
      </>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <Matrix days={days} bot={bot} onOpenConversation={onOpenConversation} />
      <ResultsByBot days={days} />
      {charts}
      <section className={CARD}>
        <button
          type="button"
          aria-expanded={jevOpen}
          onClick={() => setJevOpen((v) => !v)}
          className="w-full border-0 bg-transparent p-0 text-left text-sm font-semibold text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {jevOpen ? "▾" : "▸"} Jev en producción
        </button>
        {jevOpen ? (
          <div className="mt-3">
            <JevInProduction days={days} />
          </div>
        ) : null}
      </section>
    </div>
  );
}
