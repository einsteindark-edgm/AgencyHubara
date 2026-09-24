/**
 * Pestaña Resumen del laboratorio (plan §5 y §11, PR 13).
 *
 * Arriba, si se puede confiar en la corrida: fidelidad del simulador (A1
 * contra A0, vara 90 %), validación del modo turno contra el scorecard de
 * producción y si calificó el juez. Después, las MISMAS gráficas de Calidad
 * LLM para el bot elegido; la comparación pareada contra el bot actual con su
 * intervalo (sin ganador si cruza el cero) y los turnos que cambiaron; y la
 * arena de los bots nuevos (latencia, caídas, costo, acuerdo con el juez).
 */

import { useState } from "react";

import {
  Chip,
  armLabel,
  apiErrorDetail,
  customerLabel,
  formatUsd,
  useRunDiff,
  useRunReport,
  useRunSummary,
  type ArenaArm,
  type LabRun,
  type RunReport,
} from "@plugins/lab/frontend/entities/lab-run";
import { toQualityFunnel } from "@/shared/lib";
import { CheckTrend, FailurePareto, StageFunnel, VerdictTiles } from "@/shared/ui";

import { conclusion, intervalText, pct, points, topChecks } from "../lib/summary-view";

interface Props {
  run: LabRun | null;
  /** Elegir un veredicto lleva a la pestaña Conversaciones. */
  onOpenConversations: () => void;
}

const CARD = "rounded-lg border border-line p-3";
const H3 = "m-0 text-sm font-semibold text-fg";

function fine(rate: number | null): string {
  return rate === null ? "—" : `${(rate * 100).toLocaleString("es-CO", { maximumFractionDigits: 1 })} %`;
}

function ms(value: number | null): string {
  return value === null ? "—" : `${Math.round(value)} ms`;
}

function Health({ report }: { report: RunReport }) {
  const fid = report.fidelity;
  const val = report.validation;
  return (
    <section aria-label="Confianza de la corrida" className="flex flex-wrap items-center gap-2 text-xs">
      {fid && fid.agreement !== null ? (
        <Chip tone={fid.ok ? "ok" : "bad"}>
          {`Fidelidad del simulador ${pct(fid.agreement)} (vara ${pct(fid.threshold)})`}
        </Chip>
      ) : (
        <Chip tone="neutral">Fidelidad del simulador: sin datos</Chip>
      )}
      {val && val.agreement !== null ? (
        <Chip tone="neutral">
          {`Modo turno vs. producción: ${pct(val.agreement)} de acuerdo en checks de código · ${val.episodes} episodios`}
        </Chip>
      ) : null}
      {report.judge ? (
        <Chip tone={report.judge.used ? "neutral" : "warn"}>
          {report.judge.used
            ? `con juez${report.judge.errors ? ` · ${report.judge.errors} llamadas fallidas` : ""}`
            : "sin juez: solo checks de código"}
        </Chip>
      ) : null}
      {fid && !fid.ok && fid.agreement !== null ? (
        <p className="m-0 w-full text-[11.5px] text-danger">
          El bot actual simulado no reproduce bien a producción: las diferencias contra él pierden valor.
        </p>
      ) : null}
    </section>
  );
}

function BotPicker({ arms, value, onChange }: { arms: string[]; value: string; onChange: (arm: string) => void }) {
  return (
    <fieldset className="m-0 flex flex-wrap gap-1 border-0 p-0" aria-label="Bot">
      {arms.map((arm) => (
        <label
          key={arm}
          className={
            "cursor-pointer rounded-md border px-2.5 py-1 text-xs " +
            (arm === value ? "border-accent bg-accent/10 text-fg" : "border-line text-fg-muted hover:text-fg")
          }
        >
          <input
            type="radio"
            name="lab-summary-bot"
            value={arm}
            checked={arm === value}
            onChange={() => onChange(arm)}
            className="sr-only"
          />
          {armLabel(arm)}
        </label>
      ))}
    </fieldset>
  );
}

function Charts({ run, arm, onOpenConversations }: { run: string; arm: string; onOpenConversations: () => void }) {
  const summary = useRunSummary(run, arm);
  const [check, setCheck] = useState<string | null>(null);
  if (summary.isPending) return <p className="text-sm text-fg-muted">Cargando las gráficas…</p>;
  if (summary.isError) {
    return <p className="text-sm text-fg-muted">{apiErrorDetail(summary.error).message ?? "No se pudieron leer las gráficas."}</p>;
  }
  const data = summary.data;
  const days = Math.max(7, data.trend[0]?.weeks.length ? data.trend[0].weeks.length * 7 : 7);
  return (
    <div className="flex flex-col gap-3">
      <p className="m-0 text-xs text-fg-muted">
        {`${data.episodes} episodios calificados turno por turno con el prefijo real como contexto.`}
        {data.pass_k && data.pass_k.k > 1 ? ` Episodios que pasan en las ${data.pass_k.k} repeticiones: ${pct(data.pass_k.rate)}.` : ""}
      </p>
      <VerdictTiles totals={data.verdicts} episodes={data.episodes} onSelectVerdict={onOpenConversations} />
      <div className="grid gap-3 xl:grid-cols-2">
        <section className={CARD} aria-labelledby="lab-pareto-title">
          <h3 id="lab-pareto-title" className={H3}>Qué arreglar primero</h3>
          <p className="mb-2 mt-1 text-[11px] text-fg-faint">Fallos por check, coloreados por nivel, con el acumulado.</p>
          <FailurePareto pareto={data.pareto} days={days} selectedCheckId={check} onSelectCheck={setCheck} />
        </section>
        <section className={CARD} aria-labelledby="lab-funnel-title">
          <h3 id="lab-funnel-title" className={H3}>Dónde terminan los episodios</h3>
          <p className="mb-2 mt-1 text-[11px] text-fg-faint">Etapa del último turno simulado y veredicto del episodio.</p>
          <StageFunnel funnel={toQualityFunnel(data.funnel)} />
        </section>
      </div>
      {data.trend.length > 0 ? (
        <section className={CARD} aria-labelledby="lab-trend-title">
          <h3 id="lab-trend-title" className={H3}>Cumplimiento por check, semana a semana</h3>
          <CheckTrend trend={data.trend} />
        </section>
      ) : null}
    </div>
  );
}

function diffLabel(key: string): string {
  const [base, cand] = key.split(":");
  return base === "A0" ? `${armLabel(base)} → ${armLabel(cand)} (fidelidad)` : `${armLabel(base)} → ${armLabel(cand)}`;
}

function Comparison({ run, keys }: { run: string; keys: string[] }) {
  const preferred = keys.find((k) => k.startsWith("A1:")) ?? keys[0];
  const [picked, setPicked] = useState<string | null>(null);
  const key = picked && keys.includes(picked) ? picked : preferred;
  const [base, cand] = key.split(":");
  const diff = useRunDiff(run, base, cand);
  const verdictTone = diff.data ? conclusion(base, cand, diff.data.episode_pass) : null;
  return (
    <section aria-label="Comparación" className={CARD}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className={H3}>Comparación pareada</h3>
        <label className="ml-auto flex items-center gap-2 text-xs text-fg-muted">
          Contra
          <select
            aria-label="Comparación"
            value={key}
            onChange={(e) => setPicked(e.target.value)}
            className="rounded-md border border-line-strong bg-canvas px-2 py-1 text-[11.5px] text-fg"
          >
            {keys.map((k) => (
              <option key={k} value={k}>
                {diffLabel(k)}
              </option>
            ))}
          </select>
        </label>
      </div>
      {diff.isPending ? <p className="text-sm text-fg-muted">Cargando la comparación…</p> : null}
      {diff.isError ? <p className="text-sm text-fg-muted">Sin comparación para estos dos bots todavía.</p> : null}
      {diff.data && verdictTone ? (
        <div className="mt-2 flex flex-col gap-2">
          <p className={"m-0 text-sm font-semibold " + (verdictTone.tone === "ok" ? "text-ok" : verdictTone.tone === "bad" ? "text-danger" : "text-fg")}>
            {verdictTone.text}
          </p>
          <p className="m-0 text-xs text-fg-muted">
            {`Episodios que pasan: ${diff.data.episode_pass.delta === null ? "—" : points(diff.data.episode_pass.delta)} · ${intervalText(diff.data.episode_pass)}`}
            {diff.data.pass_k ? ` · pass^${diff.data.pass_k.cand.k}: ${pct(diff.data.pass_k.base.rate)} → ${pct(diff.data.pass_k.cand.rate)}` : ""}
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
                      <th scope="row" className="py-1 pr-3 text-left font-semibold text-fg">{c.check_id}</th>
                      <td className="py-1 pr-3">{c.delta === null ? "—" : points(c.delta)}</td>
                      <td className="py-1 pr-3 text-fg-muted">{intervalText(c)}</td>
                      <td className="py-1">{c.conclusive ? "sí" : "aún no"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {diff.data.changed_turns.length > 0 ? (
            <div>
              <h4 className="m-0 mb-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
                {`Turnos que cambiaron de veredicto (${diff.data.changed_turns.length})`}
              </h4>
              <ul className="m-0 flex list-none flex-col gap-1 p-0 text-xs">
                {diff.data.changed_turns.slice(0, 15).map((t) => (
                  <li key={`${t.session_id}:${t.episode_id}:${t.turn}`} className="flex flex-wrap items-center gap-2">
                    <span>{customerLabel(t.session_id)}</span>
                    <span className="font-mono text-fg-muted">{`${t.episode_id} · turno ${t.turn}`}</span>
                    <span>{`${t.base} → ${t.cand}`}</span>
                    {t.checks.length ? <span className="text-fg-faint">{t.checks.join(", ")}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function Arena({ arena }: { arena: Record<string, ArenaArm> }) {
  const arms = Object.keys(arena).sort();
  return (
    <section aria-label="Arena de clasificadores" className={CARD}>
      <h3 className={H3}>Arena de clasificadores</h3>
      <p className="mb-2 mt-1 text-[11px] text-fg-faint">
        Repetición 1. El acuerdo lleva los asuntos del juez (texto libre) a los códigos del clasificador por palabras
        clave: es aproximado y sirve para comparar los bots entre sí.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-xs tabular-nums">
          <thead>
            <tr className="text-left text-fg-faint">
              {["Bot", "Perfil", "p95", "Caídas", "Costo por turno", "Complemento", "Ronda extra", "Acuerdo (F1)", "Brier"].map((h) => (
                <th key={h} className="py-1 pr-3 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {arms.map((arm) => {
              const a = arena[arm];
              const m = a.metrics[0];
              return (
                <tr key={arm} className="border-t border-line">
                  <th scope="row" className="py-1 pr-3 text-left font-semibold text-fg">{armLabel(arm)}</th>
                  <td className="py-1 pr-3 font-mono">{a.profile ?? "—"}</td>
                  <td className="py-1 pr-3">{ms(m?.perception?.p95_ms ?? null)}</td>
                  <td className="py-1 pr-3">{fine(m?.perception?.fallback_rate ?? null)}</td>
                  <td className="py-1 pr-3">{m?.cost_per_turn_usd === null || !m ? "—" : formatUsd(m.cost_per_turn_usd)}</td>
                  <td className="py-1 pr-3">{fine(m?.complement_rate ?? null)}</td>
                  <td className="py-1 pr-3">{fine(m?.extra_round_rate ?? null)}</td>
                  <td className="py-1 pr-3">{pct(a.topics.f1)}</td>
                  <td className="py-1">{a.topics.calibration.brier === null ? "—" : a.topics.calibration.brier.toLocaleString("es-CO")}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function RunSummary({ run, onOpenConversations }: Props) {
  const report = useRunReport(run?.run_id ?? null);
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
  const arms = data.arms;
  const arm = picked && arms.includes(picked) ? picked : arms.includes("A1") ? "A1" : (arms[0] ?? "A0");
  return (
    <div className="flex flex-col gap-3">
      <Health report={data} />
      {data.mode !== "turn" ? (
        <p className="m-0 text-xs text-fg-muted">
          Los bots simulados todavía no se calificaron: se muestra el scorecard de producción.
        </p>
      ) : null}
      <BotPicker arms={arms} value={arm} onChange={setPicked} />
      <Charts run={run.run_id} arm={arm} onOpenConversations={onOpenConversations} />
      {data.diffs.length > 0 ? <Comparison run={run.run_id} keys={data.diffs} /> : null}
      {Object.keys(data.arena).length > 0 ? <Arena arena={data.arena} /> : null}
    </div>
  );
}
