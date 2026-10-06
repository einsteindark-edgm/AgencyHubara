/**
 * Pestaña Resumen del laboratorio. Arriba (2026-10-01), las tres gráficas de
 * Calidad LLM del bot elegido (por defecto, el bot nuevo con Jev):
 *  · cumplimiento por check, semana a semana (las semanas son las de las
 *    conversaciones del banco);
 *  · dónde terminan los episodios;
 *  · la matriz episodios × checks, con sus filtros; una fila abre la
 *    conversación.
 * Debajo, plegada, la comparación entre bots (revisión 2026-09-29: el
 * operador no entendía «IC 95 %», «pp», «Holm», «F1», «Brier» ni «vara»):
 * ¿el bot nuevo es mejor? y qué turnos cambiaron, cómo le fue a cada bot, en
 * qué se diferencian, si se puede confiar en la corrida y cómo anduvo Jev.
 */

import { useMemo, useState, type ReactNode } from "react";

import {
  armLabel,
  apiErrorDetail,
  checkView,
  customerLabel,
  formatUsd,
  LevelPill,
  useCheckCatalog,
  useRunDiff,
  useRunReport,
  useRunScorecards,
  useRunSummaries,
  useRunSummary,
  type ArenaArm,
  type ArmSummary,
  type CheckCatalog,
  type LabRun,
  type RunReport,
} from "@plugins/lab/frontend/entities/lab-run";
import { qualityStageLabel, toQualityFunnel } from "@/shared/lib";
import { CheckTrend, ComplianceMatrixLegend, ComplianceMatrixTable, StageFunnel } from "@/shared/ui";

import { filterRows, finalStages, matrixGroups, rowKey, toRowView, type VerdictFilter } from "../lib/matrix-view";
import { checkDifferences, conclusion, intervalText, MIN_CONCLUSIVE_SESSIONS, pct, points, topChecks } from "../lib/summary-view";

interface Props {
  run: LabRun | null;
  /** Abre la pestaña Conversaciones (con esa conversación elegida, si viene). */
  onOpenConversations: (sid?: string) => void;
}

const CARD = "rounded-lg border border-line p-3";
const H3 = "m-0 text-sm font-semibold text-fg";
const NOTE = "m-0 text-[11.5px] text-fg-muted";

function fine(rate: number | null): string {
  return rate === null ? "—" : `${(rate * 100).toLocaleString("es-CO", { maximumFractionDigits: 1 })} %`;
}

function seconds(ms: number | null): string {
  return ms === null ? "—" : `${(ms / 1000).toLocaleString("es-CO", { maximumFractionDigits: 1 })} s`;
}

// ── 1. ¿El bot nuevo es mejor? ───────────────────────────────────────────────

function comparisonLabel(key: string): string {
  const [base, cand] = key.split(":");
  return base === "A0" ? `${armLabel(cand)} contra producción (¿el simulador reproduce bien?)` : `${armLabel(cand)} contra ${armLabel(base)}`;
}

function Answer({ run, keys, catalog, onOpenConversations }: { run: string; keys: string[]; catalog: CheckCatalog | undefined; onOpenConversations: (sid?: string) => void }) {
  const preferred = keys.find((k) => k.startsWith("A1:")) ?? keys[0];
  const [picked, setPicked] = useState<string | null>(null);
  const key = picked && keys.includes(picked) ? picked : preferred;
  const [base, cand] = key.split(":");
  const diff = useRunDiff(run, base, cand);
  const verdict = diff.data ? conclusion(base, cand, diff.data.episode_pass) : null;
  const nameOf = (id: string) => checkView(catalog, { check_id: id, verdict: "falla", turn: null, evidence: null, critique: null, source: null, topics: [] }).name;

  return (
    <section aria-label="¿El bot nuevo es mejor?" className={CARD}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className={H3}>¿El bot nuevo es mejor?</h3>
        {keys.length > 1 ? (
          <label className="ml-auto flex items-center gap-2 text-xs text-fg-muted">
            Comparar
            <select
              aria-label="Comparación"
              value={key}
              onChange={(e) => setPicked(e.target.value)}
              className="rounded-md border border-line-strong bg-canvas px-2 py-1 text-[11.5px] text-fg"
            >
              {keys.map((k) => (
                <option key={k} value={k}>
                  {comparisonLabel(k)}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
      {diff.isPending ? <p className="text-sm text-fg-muted">Cargando la comparación…</p> : null}
      {diff.isError ? <p className="text-sm text-fg-muted">Todavía no hay comparación entre estos dos bots.</p> : null}
      {diff.data && verdict ? (
        <div className="mt-2 grid gap-2.5">
          <p className={"m-0 text-[14px] font-semibold " + (verdict.tone === "ok" ? "text-ok" : verdict.tone === "bad" ? "text-danger" : "text-fg")}>{verdict.text}</p>
          <p className={NOTE}>
            {`Se compara cuántas conversaciones terminan en PASA con cada bot, sobre las mismas conversaciones reales (${diff.data.episode_pass.sessions} en común).`}
          </p>

          {diff.data.changed_turns.length > 0 ? (
            <div>
              <h4 className="m-0 mb-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
                {`Turnos que cambiaron de resultado (${diff.data.changed_turns.length})`}
              </h4>
              <ul aria-label="Turnos que cambiaron" className="m-0 grid list-none gap-1.5 p-0 text-[12.5px]">
                {diff.data.changed_turns.slice(0, 15).map((t) => (
                  <li key={`${t.session_id}:${t.episode_id}:${t.turn}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-line px-2.5 py-1.5">
                    <span className="font-medium text-fg">{`${customerLabel(t.session_id)} · turno ${t.turn}`}</span>
                    <span className="text-fg-muted">{`${armLabel(base)}: ${t.base} → ${armLabel(cand)}: ${t.cand}`}</span>
                    {t.checks.map((id) => (
                      <span key={id} className="text-fg-soft">
                        {nameOf(id)}
                      </span>
                    ))}
                    <button
                      type="button"
                      onClick={() => onOpenConversations(t.session_id)}
                      className="ml-auto rounded-md border border-line-strong bg-white/[0.06] px-2 py-1 text-[11.5px] text-fg hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    >
                      Ver conversación
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <details className="text-[12px]">
            <summary className="cursor-pointer select-none text-fg-muted">Detalle estadístico</summary>
            <div className="mt-2 grid gap-2">
              <p className={NOTE}>
                {`Diferencia en conversaciones que pasan: ${diff.data.episode_pass.delta === null ? "—" : points(diff.data.episode_pass.delta)} · ${intervalText(diff.data.episode_pass)}`}
              </p>
              {diff.data.checks.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-xs tabular-nums">
                    <caption className="sr-only">Checks que más se movieron</caption>
                    <thead>
                      <tr className="text-left text-fg-faint">
                        <th className="py-1 pr-3 font-medium">Check</th>
                        <th className="py-1 pr-3 font-medium">Cambio</th>
                        <th className="py-1 pr-3 font-medium">Intervalo</th>
                        <th className="py-1 font-medium">¿Concluyente?</th>
                      </tr>
                    </thead>
                    <tbody>
                      {topChecks(diff.data.checks, 12).map((c) => (
                        <tr key={c.check_id} className="border-t border-line">
                          <th scope="row" className="py-1 pr-3 text-left font-medium text-fg">{nameOf(c.check_id)}</th>
                          <td className="py-1 pr-3">{c.delta === null ? "—" : points(c.delta)}</td>
                          <td className="py-1 pr-3 text-fg-muted">{intervalText(c)}</td>
                          <td className="py-1">{c.conclusive ? "sí" : "aún no"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
              <p className={NOTE}>
                {`«pp» son puntos porcentuales. Concluyente: al menos ${MIN_CONCLUSIVE_SESSIONS} conversaciones en común y el intervalo del 95 % sin cruzar el cero, corregido por comparaciones múltiples (Holm).`}
              </p>
            </div>
          </details>
        </div>
      ) : null}
    </section>
  );
}

// ── 2. Resultado por bot ─────────────────────────────────────────────────────

function ResultsByBot({ arms, summaries }: { arms: string[]; summaries: Record<string, ArmSummary | undefined> }) {
  const rows = arms.filter((a) => summaries[a]);
  if (rows.length === 0) return null;
  return (
    <section className={CARD} aria-labelledby="lab-results-title">
      <h3 id="lab-results-title" className={H3}>Cómo le fue a cada bot</h3>
      <div className="mt-2 overflow-x-auto">
        <table aria-label="Resultado por bot" className="w-full border-collapse text-[12.5px] tabular-nums">
          <thead>
            <tr className="text-left text-[11px] text-fg-faint">
              {["Bot", "Conversaciones", "Pasan", "En alerta", "Fallan", "Sin datos"].map((h) => (
                <th key={h} className="py-1 pr-3 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((arm) => {
              const s = summaries[arm] as ArmSummary;
              return (
                <tr key={arm} className="border-t border-line">
                  <th scope="row" className="py-1.5 pr-3 text-left font-medium text-fg">{armLabel(arm)}</th>
                  <td className="py-1.5 pr-3">{s.episodes}</td>
                  <td className="py-1.5 pr-3 text-ok">{s.verdicts.PASA}</td>
                  <td className="py-1.5 pr-3 text-warn">{s.verdicts.ALERTA}</td>
                  <td className="py-1.5 pr-3 text-danger">{s.verdicts.FALLA}</td>
                  <td className="py-1.5 pr-3 text-fg-muted">{s.verdicts.SIN_DATOS}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className={"mt-2 " + NOTE}>Pasa: cumplió todo lo importante · En alerta: falló algo importante (un check mayor) · Falla: falló algo crítico.</p>
    </section>
  );
}

// ── 3. En qué se diferencian ─────────────────────────────────────────────────

function Differences({ arms, summaries }: { arms: string[]; summaries: Record<string, ArmSummary | undefined> }) {
  const shown = arms.filter((a) => summaries[a]);
  const rows = checkDifferences(summaries, shown);
  if (shown.length === 0) return null;
  return (
    <section className={CARD} aria-labelledby="lab-diff-title">
      <h3 id="lab-diff-title" className={H3}>En qué se diferencian</h3>
      <p className={"mb-2 mt-1 " + NOTE}>Cada check que falló al menos una vez: en cuántas conversaciones falló con cada bot, de las que le aplicaban.</p>
      {rows.length === 0 ? (
        <p className="m-0 text-[12.5px] text-ok">Ningún check falló con ningún bot en esta corrida.</p>
      ) : (
        <div className="overflow-x-auto">
          <table aria-label="En qué se diferencian los bots" className="w-full border-collapse text-[12.5px] tabular-nums">
            <thead>
              <tr className="text-left text-[11px] text-fg-faint">
                <th className="py-1 pr-3 font-medium">Check</th>
                {shown.map((a) => (
                  <th key={a} className="py-1 pr-3 font-medium">{armLabel(a)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.checkId} className="border-t border-line align-top">
                  <th scope="row" className="py-1.5 pr-3 text-left font-normal">
                    <span className="flex flex-wrap items-center gap-1.5">
                      <span className="text-fg">{row.name}</span>
                      <LevelPill level={row.level} />
                    </span>
                  </th>
                  {row.cells.map((c, k) => (
                    <td
                      key={shown[k]}
                      className={"py-1.5 pr-3 " + (c === null || c.applicable === 0 ? "text-fg-faint" : c.failed === 0 ? "text-ok" : row.level === "menor" ? "text-fg-soft" : "text-danger")}
                    >
                      {c === null || c.applicable === 0 ? "—" : `${c.failed} de ${c.applicable}`}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ── 4. ¿Se puede confiar en la corrida? ──────────────────────────────────────

function Trust({ report }: { report: RunReport }) {
  const fid = report.fidelity;
  const val = report.validation;
  const lines: Array<{ ok: boolean | null; text: string }> = [];
  if (fid && fid.agreement !== null) {
    lines.push(
      fid.ok
        ? { ok: true, text: `El simulador reproduce bien a producción: coincide en ${pct(fid.agreement)} de los checks (mínimo exigido: ${pct(fid.threshold)}).` }
        : {
            ok: false,
            text: `El simulador no reproduce bien a producción: coincide en ${pct(fid.agreement)} de los checks (mínimo exigido: ${pct(fid.threshold)}). Las diferencias contra el bot nuevo pierden valor.`,
          },
    );
  } else {
    lines.push({ ok: null, text: "Todavía no se sabe si el simulador reproduce bien a producción." });
  }
  if (val && val.agreement !== null) {
    lines.push({
      ok: null,
      text: `Calificar turno por turno da lo mismo que la calificación de producción en ${pct(val.agreement)} de los checks automáticos (${val.episodes} conversaciones).`,
    });
  }
  if (report.judge) {
    lines.push(
      report.judge.used
        ? { ok: true, text: `Calificada con reglas automáticas y con el juez.${report.judge.errors ? ` ${report.judge.errors} llamadas al juez fallaron.` : ""}` }
        : { ok: false, text: "Calificada solo con reglas automáticas: los checks que necesitan juez quedaron sin calificar." },
    );
  }
  return (
    <section aria-label="¿Se puede confiar en esta corrida?" className={CARD}>
      <h3 className={H3}>¿Se puede confiar en esta corrida?</h3>
      <ul className="m-0 mt-2 grid list-none gap-1 p-0 text-[12.5px]">
        {lines.map((l, k) => (
          <li key={k} className="flex gap-2">
            <span aria-hidden="true" className={l.ok === true ? "text-ok" : l.ok === false ? "text-danger" : "text-fg-faint"}>
              {l.ok === true ? "✓" : l.ok === false ? "✕" : "·"}
            </span>
            <span className="text-fg-soft">{l.text}</span>
          </li>
        ))}
      </ul>
      {report.mode !== "turn" ? <p className={"mt-2 " + NOTE}>Los bots simulados todavía no se calificaron: se muestra el scorecard de producción.</p> : null}
      {report.mode === "turn" && report.arms_pending.length > 0 ? (
        <p className={"mt-2 " + NOTE}>{`${report.arms_pending.map(armLabel).join(", ")}: la corrida no alcanzó a simularlo (queda pendiente; no cuenta como falla).`}</p>
      ) : null}
    </section>
  );
}

// ── 5. Jev en esta corrida ───────────────────────────────────────────────────

function JevReport({ arena }: { arena: Record<string, ArenaArm> }) {
  const arms = Object.keys(arena).sort();
  return (
    <section aria-label="Jev en esta corrida" className={CARD}>
      <h3 className={H3}>Jev en esta corrida</h3>
      {arms.map((arm) => {
        const a = arena[arm];
        const m = a.metrics[0];
        const rows: Array<[string, string]> = [
          ["Versión del motor", a.profile ?? "—"],
          [
            "Tiempo de respuesta",
            m?.perception?.p95_ms != null
              ? `El 95 % de las respuestas de Jev llegó en menos de ${seconds(m.perception.p95_ms)} (la mitad, en menos de ${seconds(m.perception.p50_ms)}).`
              : "—",
          ],
          ["No respondió a tiempo o falló", m?.perception ? `${fine(m.perception.fallback_rate)} de las preguntas: en esas decidió la regla de hoy.` : "—"],
          [
            "Costo por turno",
            m && m.cost_per_turn_usd !== null
              ? `${formatUsd(m.cost_per_turn_usd, 4)}${m.perception_cost_per_turn_usd !== null ? ` (Jev: ${formatUsd(m.perception_cost_per_turn_usd, 4)})` : ""}`
              : "—",
          ],
          ["Mensaje de complemento", m ? `${fine(m.complement_rate)} de los turnos (cuando la respuesta dejó un asunto sin cubrir).` : "—"],
          ["Segunda vuelta del modelo", m ? `${fine(m.extra_round_rate)} de los turnos.` : "—"],
          ["Acuerdo con el juez sobre los asuntos del cliente", `${pct(a.topics.f1)} (aproximado: sirve para comparar bots entre sí).`],
        ];
        return (
          <div key={arm} className="mt-2">
            {arms.length > 1 ? <h4 className="m-0 mb-1 text-[12px] font-semibold text-fg">{armLabel(arm)}</h4> : null}
            <dl className="m-0 grid grid-cols-1 gap-x-4 gap-y-1 text-[12.5px] min-[640px]:grid-cols-[max-content_1fr]">
              {rows.map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-fg-muted">{k}</dt>
                  <dd className="m-0 text-fg-soft">{v}</dd>
                </div>
              ))}
            </dl>
            <details className="mt-2 text-[12px]">
              <summary className="cursor-pointer select-none text-fg-muted">Detalle técnico</summary>
              <p className={"mt-1 " + NOTE}>
                {`Precisión ${pct(a.topics.precision)} · cobertura ${pct(a.topics.recall)} · calibración de las probabilidades (0 = perfecta) ${
                  a.topics.calibration.brier === null ? "—" : a.topics.calibration.brier.toLocaleString("es-CO")
                } · errores ${m?.errors ?? 0} en ${m?.turns ?? 0} turnos.`}
              </p>
            </details>
          </div>
        );
      })}
    </section>
  );
}

// ── Las tres gráficas de Calidad LLM, por bot ────────────────────────────────

function BotPicker({ arms, value, onChange }: { arms: string[]; value: string; onChange: (arm: string) => void }) {
  return (
    <fieldset className="m-0 flex flex-wrap gap-1 border-0 p-0" aria-label="Bot">
      {arms.map((arm) => (
        <label
          key={arm}
          className={"cursor-pointer rounded-md border px-2.5 py-1 text-xs " + (arm === value ? "border-accent bg-accent/10 text-fg" : "border-line text-fg-muted hover:text-fg")}
        >
          <input type="radio" name="lab-summary-bot" value={arm} checked={arm === value} onChange={() => onChange(arm)} className="sr-only" />
          {armLabel(arm)}
        </label>
      ))}
    </fieldset>
  );
}

function Charts({ run, arm }: { run: string; arm: string }) {
  const summary = useRunSummary(run, arm);
  if (summary.isPending) return <p className="text-sm text-fg-muted">Cargando las gráficas…</p>;
  if (summary.isError) return <p className="text-sm text-fg-muted">{apiErrorDetail(summary.error).message ?? "No se pudieron leer las gráficas."}</p>;
  const data = summary.data;
  // Dos columnas en pantallas anchas, como en Calidad LLM: el embudo es un SVG
  // que escala con el ancho y a lo ancho de la ventana se ve gigante.
  return (
    <div className="grid items-start gap-3 xl:grid-cols-2">
      <section className={CARD} aria-labelledby="lab-trend-title">
        <h3 id="lab-trend-title" className={H3}>Cumplimiento por check, semana a semana</h3>
        <CheckTrend trend={data.trend} />
      </section>
      <section className={CARD} aria-labelledby="lab-funnel-title">
        <h3 id="lab-funnel-title" className={H3}>Dónde terminan los episodios</h3>
        <p className="mb-2 mt-1 text-[11px] text-fg-faint">Etapa final de cada episodio y su veredicto.</p>
        <StageFunnel funnel={toQualityFunnel(data.funnel)} />
      </section>
    </div>
  );
}

const VERDICT_OPTIONS: ReadonlyArray<{ value: VerdictFilter; label: string }> = [
  { value: "todos", label: "Todos" },
  { value: "FALLA", label: "Falla" },
  { value: "ALERTA", label: "Alerta" },
  { value: "PASA", label: "Pasa" },
];

const ROW_CAP = 120;

function Matrix({ run, arm, catalog, onOpenConversations }: { run: string; arm: string; catalog: CheckCatalog | undefined; onOpenConversations: (sid?: string) => void }) {
  const cards = useRunScorecards(run, arm);
  const [verdict, setVerdict] = useState<VerdictFilter>("todos");
  const [stage, setStage] = useState<string | null>(null);
  const [onlyFailing, setOnlyFailing] = useState(false);
  const all = useMemo(() => cards.data?.rows ?? [], [cards.data]);
  const rows = useMemo(() => filterRows(all, { verdict, stage }), [all, verdict, stage]);
  const groups = useMemo(() => matrixGroups(catalog?.checks ?? [], rows, { onlyFailing }), [catalog, rows, onlyFailing]);
  const stages = useMemo(() => finalStages(all), [all]);
  const sessions = useMemo(() => new Map(rows.map((r) => [rowKey(r), r.session_id])), [rows]);

  let body: ReactNode;
  if (cards.isPending) body = <p className="text-sm text-fg-muted">Cargando la matriz…</p>;
  else if (cards.isError) body = <p className="text-sm text-fg-muted">{apiErrorDetail(cards.error).message ?? "No se pudo leer la matriz."}</p>;
  else if (all.length === 0) body = <p className="text-sm text-fg-muted">Este bot no tiene episodios calificados en esta corrida.</p>;
  else if (rows.length === 0) body = <p className="text-sm text-fg-muted">Ningún episodio coincide con los filtros.</p>;
  else {
    body = (
      <ComplianceMatrixTable
        groups={groups}
        rows={rows.map(toRowView)}
        selectedKey={null}
        onSelectRow={(view) => onOpenConversations(sessions.get(view.key))}
        rowCap={ROW_CAP}
        resetKey={`${arm}|${verdict}|${stage ?? ""}`}
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

// ── La pestaña ───────────────────────────────────────────────────────────────

/** Por defecto, el bot nuevo con Jev; si la corrida no lo trae, el bot actual simulado. */
function defaultArm(arms: string[]): string {
  return ["B", "A1"].find((a) => arms.includes(a)) ?? arms[0] ?? "A0";
}

export function RunSummary({ run, onOpenConversations }: Props) {
  const report = useRunReport(run?.run_id ?? null);
  const catalog = useCheckCatalog();
  const arms = report.data?.arms ?? [];
  const summaries = useRunSummaries(run?.run_id ?? null, arms);
  const [picked, setPicked] = useState<string | null>(null);
  if (!run) return <p className="text-sm text-fg-muted">Todavía no hay corridas.</p>;
  if (report.isPending) return <p className="text-sm text-fg-muted">Cargando el resumen…</p>;
  if (report.isError) {
    const { status } = apiErrorDetail(report.error);
    return (
      <p className="text-sm text-fg-muted">
        {status === 404 ? "Esta corrida todavía no publicó su resumen." : "No se pudo leer el resumen de la corrida."}
      </p>
    );
  }
  const data = report.data;
  const loaded = Object.fromEntries(arms.map((a) => [a, summaries[a]?.data])) as Record<string, ArmSummary | undefined>;
  const arm = picked && arms.includes(picked) ? picked : defaultArm(arms);
  return (
    <div className="flex flex-col gap-3">
      <BotPicker arms={arms} value={arm} onChange={setPicked} />
      <Charts key={`charts:${arm}`} run={run.run_id} arm={arm} />
      <Matrix key={`matrix:${arm}`} run={run.run_id} arm={arm} catalog={catalog.data} onOpenConversations={onOpenConversations} />
      <details className={CARD}>
        <summary className="cursor-pointer select-none text-sm font-semibold text-fg">Comparación entre bots, confianza y Jev</summary>
        <div className="mt-3 flex flex-col gap-3">
          {data.diffs.length > 0 ? <Answer run={run.run_id} keys={data.diffs} catalog={catalog.data} onOpenConversations={onOpenConversations} /> : null}
          <ResultsByBot arms={arms} summaries={loaded} />
          <Differences arms={arms} summaries={loaded} />
          <div className="grid gap-3 xl:grid-cols-2">
            <Trust report={data} />
            {Object.keys(data.arena).length > 0 ? <JevReport arena={data.arena} /> : null}
          </div>
        </div>
      </details>
    </div>
  );
}
