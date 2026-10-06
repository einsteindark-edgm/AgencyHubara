/**
 * Resumen de Calidad LLM con la vista del laboratorio, sobre producción
 * (decisión del operador, 2026-10-02): las dos gráficas (cumplimiento por
 * check semana a semana; dónde terminan los episodios) y la matriz episodios ×
 * checks con sus filtros, del bot elegido arriba. Una fila abre esa
 * conversación. Debajo, a pedido, cómo le fue a cada bot y cómo anduvo Jev en
 * los turnos reales (el informe de Jev del laboratorio).
 */

import { useMemo, useState, type ReactNode } from "react";

import { useCheckStats, type CheckStats } from "@plugins/agents_admin/frontend/entities/check-stats";
import { BOT_LABEL, useJevReport, type JevReport, type QualityBot } from "@plugins/agents_admin/frontend/entities/production-quality";
import { useCheckRegistry, useScorecards } from "@plugins/agents_admin/frontend/entities/scorecard";
import {
  capabilityLabel,
  fallbackReasonLabel,
  filterMatrixRows,
  formatUsd,
  matrixFinalStages,
  matrixGroups,
  matrixRowKey,
  qualityStageLabel,
  toMatrixRowView,
  toQualityFunnel,
  type MatrixVerdictFilter,
} from "@/shared/lib";
import { CheckTrend, ComplianceMatrixLegend, ComplianceMatrixTable, StageFunnel } from "@/shared/ui";

interface Props {
  days: number;
  bot: QualityBot | null;
  /** Abre la pestaña Conversaciones con esa conversación elegida. */
  onOpenConversation: (sid: string) => void;
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

function Charts({ stats }: { stats: CheckStats }) {
  // Dos columnas en pantallas anchas: el embudo es un SVG que escala con el
  // ancho y a lo ancho de la ventana se ve gigante.
  return (
    <div className="grid items-start gap-3 xl:grid-cols-2">
      <section className={CARD} aria-labelledby="quality-trend-title">
        <h3 id="quality-trend-title" className={H3}>Cumplimiento por check, semana a semana</h3>
        <CheckTrend trend={stats.trend} />
      </section>
      <section className={CARD} aria-labelledby="quality-funnel-title">
        <h3 id="quality-funnel-title" className={H3}>Dónde terminan los episodios</h3>
        <p className="mb-2 mt-1 text-[11px] text-fg-faint">Etapa final de cada episodio y su veredicto.</p>
        <StageFunnel funnel={toQualityFunnel(stats.funnel)} />
      </section>
    </div>
  );
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
  const sessions = useMemo(() => new Map(rows.map((r) => [matrixRowKey(r), r.session_id])), [rows]);

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
          const sid = sessions.get(view.key);
          if (sid) onOpenConversation(sid);
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
      <ComplianceMatrixLegend hint="Elige una fila para abrir la conversación." />
    </section>
  );
}

// ── Cómo le fue a cada bot ───────────────────────────────────────────────────

function ResultsByBot({ days }: { days: number }) {
  const current = useCheckStats(days, "actual");
  const jev = useCheckStats(days, "nuevo");
  const rows: Array<[QualityBot, CheckStats | undefined]> = [
    ["actual", current.data],
    ["nuevo", jev.data],
  ];
  return (
    <section className={CARD} aria-labelledby="quality-results-title">
      <h3 id="quality-results-title" className={H3}>Cómo le fue a cada bot</h3>
      <div className="mt-2 overflow-x-auto">
        <table aria-label="Resultado por bot" className="w-full border-collapse text-[12.5px] tabular-nums">
          <thead>
            <tr className="text-left text-[11px] text-fg-faint">
              {["Bot", "Episodios", "Pasan", "En alerta", "Fallan", "Sin datos"].map((h) => (
                <th key={h} className="py-1 pr-3 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(([bot, s]) => (
              <tr key={bot} className="border-t border-line">
                <th scope="row" className="py-1.5 pr-3 text-left font-medium text-fg">{BOT_LABEL[bot]}</th>
                <td className="py-1.5 pr-3">{s ? s.episodes : "…"}</td>
                <td className="py-1.5 pr-3 text-ok">{s ? s.verdicts.PASA : ""}</td>
                <td className="py-1.5 pr-3 text-warn">{s ? s.verdicts.ALERTA : ""}</td>
                <td className="py-1.5 pr-3 text-danger">{s ? s.verdicts.FALLA : ""}</td>
                <td className="py-1.5 pr-3 text-fg-muted">{s ? s.verdicts.SIN_DATOS : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className={"mt-2 " + NOTE}>
        Pasa: cumplió todo lo importante · En alerta: falló algo importante (un check mayor) · Falla: falló algo crítico. Son conversaciones distintas: cada
        una la atendió un solo bot.
      </p>
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
  const [more, setMore] = useState(false);

  let charts: ReactNode;
  if (stats.isPending) charts = <p className="text-sm text-fg-muted">Cargando las gráficas…</p>;
  else if (stats.isError) charts = <p className="text-sm text-fg-muted">No se pudieron leer las gráficas.</p>;
  else if (stats.data.episodes === 0) charts = <p className="rounded-lg border border-dashed border-line-strong p-4 text-sm text-fg-muted">{emptyText(bot)}</p>;
  else charts = <Charts stats={stats.data} />;

  return (
    <div className="flex flex-col gap-3">
      {charts}
      <Matrix days={days} bot={bot} onOpenConversation={onOpenConversation} />
      <section className={CARD}>
        <button
          type="button"
          aria-expanded={more}
          onClick={() => setMore((v) => !v)}
          className="w-full border-0 bg-transparent p-0 text-left text-sm font-semibold text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {more ? "▾" : "▸"} Cómo le fue a cada bot y a Jev
        </button>
        {more ? (
          <div className="mt-3 grid gap-3 xl:grid-cols-2">
            <ResultsByBot days={days} />
            <JevInProduction days={days} />
          </div>
        ) : null}
      </section>
    </div>
  );
}
