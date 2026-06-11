import { useMemo } from "react";

import {
  episodeUnitKey,
  useConversationEvals,
  useEvalTranscript,
  type ConversationEval,
  type EpisodeEvalRun,
} from "@plugins/agents_admin/frontend/entities/episode-eval";
import { Icon } from "@/shared/ui";

/**
 * Evaluaciones por episodio (la vista central de "Calidad LLM").
 *
 * Responde las tres preguntas del operador:
 *   1. ¿QUÉ conversación generó los malos scores? — lista por episodio, las
 *      falladas arriba, con métricas falladas y razón del juez.
 *   2. ¿Mejoró o quedó igual? — timeline de evals del MISMO episodio
 *      (re-evaluado mientras la conversación seguía viva) con su evolución.
 *   3. ¿Qué se va a escoger para el golden? — badge de candidata + salto a la
 *      pestaña de curación.
 */

interface Props {
  /** Ventana (días) de conversaciones — compartida con la tendencia. */
  windowDays: number;
  /** Día seleccionado en la tendencia (filtra la lista a episodios evaluados ese día). */
  dateFilter: string | null;
  onClearDateFilter: () => void;
  /** Episodio seleccionado (`<session>::<episode>`), lifted: lo comparte el
   *  selector de la tendencia. null = nada seleccionado. */
  selectedKey: string | null;
  onSelectKey: (key: string | null) => void;
  /** "Solo con fallas", lifted: lo activa el badge de alerta de AgentsQuality. */
  onlyFailing: boolean;
  onOnlyFailingChange: (value: boolean) => void;
  /** Abre el candidato en la pestaña de curación de goldens. */
  onOpenCandidate: (candidateId: string) => void;
}

function scoreColor(avg: number | null): string {
  if (avg === null) return "text-fg-faint";
  if (avg >= 0.7) return "text-green";
  if (avg >= 0.5) return "text-orange";
  return "text-red";
}

const TREND_LABEL: Record<string, { glyph: string; text: string; cls: string }> = {
  up: { glyph: "↑", text: "mejoró entre evals", cls: "text-green" },
  down: { glyph: "↓", text: "empeoró entre evals", cls: "text-red" },
  flat: { glyph: "→", text: "quedó igual entre evals", cls: "text-fg-faint" },
  single: { glyph: "·", text: "una sola eval", cls: "text-fg-faint" },
};

function sessionLabel(sessionId: string): string {
  return sessionId.startsWith("wa_") ? sessionId.slice(3) : sessionId;
}

export function EpisodeEvals({
  windowDays,
  dateFilter,
  onClearDateFilter,
  selectedKey,
  onSelectKey,
  onlyFailing,
  onOnlyFailingChange,
  onOpenCandidate,
}: Props) {
  const { data, isLoading, isError } = useConversationEvals(windowDays, "online");

  const allConversations = data?.conversations ?? [];
  // Cliente en foco: derivado del episodio seleccionado (`<session>::<episode>`).
  // Cuando hay uno, la lista se acota a SUS episodios (cascada cliente→episodio).
  const sessionFilter = selectedKey ? selectedKey.split("::")[0] : null;
  const conversations = useMemo(() => {
    let list = allConversations;
    if (sessionFilter) list = list.filter((c) => c.session_id === sessionFilter);
    if (onlyFailing) list = list.filter((c) => !c.last_passed || c.is_candidate);
    if (dateFilter) list = list.filter((c) => c.evals.some((e) => e.date === dateFilter));
    return list;
  }, [allConversations, sessionFilter, onlyFailing, dateFilter]);

  // El detalle se busca en la lista COMPLETA (no la filtrada): un episodio
  // seleccionado desde el selector de la tendencia debe verse aunque los
  // filtros locales de la lista lo escondan.
  const selected = allConversations.find((c) => episodeUnitKey(c) === selectedKey) ?? null;

  return (
    <div className="flex h-full min-h-0 w-full text-fg">
      {/* Lista de conversaciones evaluadas */}
      <aside className="flex w-96 shrink-0 flex-col overflow-hidden border-r border-line">
        <header className="shrink-0 space-y-2 p-3">
          <div className="flex items-center gap-2 px-1">
            <Icon.timeline />
            <h2 className="text-sm font-semibold">Conversaciones evaluadas</h2>
            <span className="ml-auto rounded-full bg-white/5 px-2 py-0.5 text-xs text-fg-muted">
              {conversations.length}
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2 px-1">
            <label className="flex cursor-pointer items-center gap-1.5 text-xs text-fg-muted">
              <input
                type="checkbox"
                checked={onlyFailing}
                onChange={(e) => onOnlyFailingChange(e.target.checked)}
                className="accent-current"
              />
              solo con fallas
            </label>
            {dateFilter && (
              <button
                type="button"
                onClick={onClearDateFilter}
                className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-2 py-0.5 text-xs text-fg"
                title="Quitar el filtro de día (viene de la tendencia)"
              >
                evaluadas el {dateFilter} <Icon.x />
              </button>
            )}
            {sessionFilter && (
              <button
                type="button"
                onClick={() => onSelectKey(null)}
                className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-2 py-0.5 text-xs text-fg"
                title="Quitar el filtro de cliente (ver todos)"
              >
                cliente {sessionLabel(sessionFilter)} <Icon.x />
              </button>
            )}
          </div>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-3 pt-0">
          {isLoading ? (
            <p className="px-2 text-sm text-fg-muted">Cargando evaluaciones…</p>
          ) : isError ? (
            <p className="px-2 text-sm text-red/80">
              No se pudieron leer las evaluaciones. Revisá que el backend esté
              arriba y reintentá.
            </p>
          ) : conversations.length === 0 ? (
            <p className="px-2 text-sm text-fg-muted">
              {dateFilter || onlyFailing
                ? "Nada que mostrar con los filtros activos."
                : "Sin evaluaciones recientes. El eval online corre varias veces " +
                  "al día y puntúa cada episodio de conversación real."}
            </p>
          ) : (
            <ul className="space-y-1">
              {conversations.map((c) => (
                <li key={episodeUnitKey(c)}>
                  <ConversationRow
                    conv={c}
                    active={episodeUnitKey(c) === selectedKey}
                    onClick={() => onSelectKey(episodeUnitKey(c))}
                  />
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>

      {/* Detalle: timeline de evals + transcript */}
      <section className="min-w-0 flex-1 overflow-y-auto p-4">
        {selected ? (
          <ConversationPane
            key={episodeUnitKey(selected)}
            conv={selected}
            threshold={data?.threshold ?? 0.7}
            onOpenCandidate={onOpenCandidate}
          />
        ) : (
          <div className="flex h-full items-center justify-center px-8 text-center text-sm text-fg-faint">
            Elegí una conversación para ver por qué puntuó así: sus evals, las
            métricas falladas con la razón del juez, y el transcript exacto que
            se evaluó.
          </div>
        )}
      </section>
    </div>
  );
}

function CandidateBadge({ conv }: { conv: ConversationEval }) {
  if (conv.candidate_status === "approved") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-green/15 px-2 py-0.5 text-[10px] font-medium text-green">
        <Icon.check /> golden aprobado
      </span>
    );
  }
  if (conv.candidate_status === "pending") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-orange/15 px-2 py-0.5 text-[10px] font-medium text-orange">
        <Icon.shield /> candidata a golden
      </span>
    );
  }
  if (conv.is_candidate) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-white/5 px-2 py-0.5 text-[10px] font-medium text-fg-muted">
        <Icon.shield /> marcada candidata
      </span>
    );
  }
  return null;
}

function ConversationRow({
  conv,
  active,
  onClick,
}: {
  conv: ConversationEval;
  active: boolean;
  onClick: () => void;
}) {
  const trend = TREND_LABEL[conv.trend] ?? TREND_LABEL.single;
  return (
    <button
      onClick={onClick}
      className={
        "w-full rounded-lg px-3 py-2 text-left transition " +
        (active ? "bg-white/10" : "hover:bg-white/5")
      }
    >
      <div className="flex items-center gap-2">
        <span className="truncate font-mono text-xs text-fg-soft">
          {sessionLabel(conv.session_id)}
        </span>
        <span className="shrink-0 rounded bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-fg-muted">
          {conv.episode_id || "sesión completa"}
        </span>
        <span
          className={
            "ml-auto shrink-0 text-xs font-semibold " + scoreColor(conv.last_avg)
          }
        >
          {conv.last_avg === null ? "—" : conv.last_avg.toFixed(2)}
        </span>
        <span className={"shrink-0 text-xs " + trend.cls} title={trend.text}>
          {trend.glyph}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] text-fg-muted">
          {conv.last_date}
          {conv.evals_count > 1 ? ` · ${conv.evals_count} evals` : ""}
          {conv.closing_tag ? ` · ${conv.closing_tag}` : ""}
        </span>
        <CandidateBadge conv={conv} />
      </div>
      {conv.failed_metrics.length > 0 && (
        <p className="mt-0.5 truncate text-[11px] text-red/80">
          falla: {conv.failed_metrics.join(", ")}
        </p>
      )}
    </button>
  );
}

/** Sparkline de los avg de las evals de ESTA conversación (¿mejoró?). */
function EvalSparkline({ evals, threshold }: { evals: EpisodeEvalRun[]; threshold: number }) {
  const W = 220;
  const H = 40;
  const PAD = 4;
  if (evals.length === 0) return null;
  const xs = (i: number) =>
    evals.length === 1 ? W / 2 : PAD + (i * (W - 2 * PAD)) / (evals.length - 1);
  const ys = (v: number) => H - PAD - v * (H - 2 * PAD);
  const line = evals.map((e, i) => `${xs(i)},${ys(e.avg)}`).join(" ");
  return (
    <svg width={W} height={H} role="img" aria-label="evolución del puntaje">
      <line
        x1={PAD}
        y1={ys(threshold)}
        x2={W - PAD}
        y2={ys(threshold)}
        stroke="var(--color-line-strong)"
        strokeWidth={1}
        strokeDasharray="3 3"
      />
      {evals.length > 1 && (
        <polyline points={line} fill="none" stroke="var(--color-accent-fg)" strokeWidth={1.5} />
      )}
      {evals.map((e, i) => (
        <circle
          key={`${e.date}T${e.ts}`}
          cx={xs(i)}
          cy={ys(e.avg)}
          r={e.avg < threshold ? 3 : 2.2}
          fill={e.avg < threshold ? "var(--color-red)" : "var(--color-accent-fg)"}
        >
          <title>{`${e.date}${e.ts ? " " + e.ts.slice(11, 16) : ""}: ${e.avg.toFixed(2)}`}</title>
        </circle>
      ))}
    </svg>
  );
}

function EvalRunCard({ run, latest }: { run: EpisodeEvalRun; latest: boolean }) {
  return (
    <details
      className="rounded-lg border border-line p-3"
      open={latest}
    >
      <summary className="flex cursor-pointer flex-wrap items-center gap-2 text-sm">
        <span className="font-medium text-fg-soft">
          {run.date}
          {run.ts ? ` ${run.ts.slice(11, 16)}` : ""}
        </span>
        {latest && (
          <span className="rounded-full bg-white/5 px-2 py-0.5 text-[10px] text-fg-muted">
            última
          </span>
        )}
        <span className={"ml-auto text-sm font-semibold " + scoreColor(run.avg)}>
          {run.avg.toFixed(2)}
        </span>
        <span
          className={
            "rounded-full px-2 py-0.5 text-[10px] font-medium " +
            (run.passed ? "bg-green/15 text-green" : "bg-red/15 text-red")
          }
        >
          {run.passed ? "pasó todas las métricas" : "falló métricas"}
        </span>
      </summary>
      <div className="mt-3 space-y-3">
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(run.metrics).map(([metric, score]) => {
            const failed = run.failed.some((f) => f.metric === metric);
            return (
              <span
                key={metric}
                className={
                  "rounded-full px-2 py-0.5 text-[11px] font-medium " +
                  (failed ? "bg-red/15 text-red" : "bg-green/15 text-green")
                }
              >
                {metric}: {score.toFixed(2)}
              </span>
            );
          })}
        </div>
        {run.failed.length > 0 && (
          <ul className="space-y-2">
            {run.failed.map((f) => (
              <li key={f.metric} className="rounded-lg bg-red/5 p-2 text-xs">
                <span className="font-semibold text-red">
                  {f.metric} ({f.score.toFixed(2)})
                </span>
                {f.reason && (
                  <p className="mt-0.5 whitespace-pre-wrap text-fg-muted">{f.reason}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </details>
  );
}

function ConversationPane({
  conv,
  threshold,
  onOpenCandidate,
}: {
  conv: ConversationEval;
  threshold: number;
  onOpenCandidate: (candidateId: string) => void;
}) {
  const trend = TREND_LABEL[conv.trend] ?? TREND_LABEL.single;
  const evalsDesc = [...conv.evals].reverse(); // más reciente primero
  const delta =
    conv.first_avg !== null && conv.last_avg !== null && conv.evals_count > 1
      ? conv.last_avg - conv.first_avg
      : null;

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <header className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="font-mono text-sm text-fg-soft">
            {sessionLabel(conv.session_id)}
          </h3>
          <span className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[11px] text-fg-muted">
            {conv.episode_id || "sesión completa"}
          </span>
          {conv.closing_tag && (
            <span className="rounded bg-white/5 px-1.5 py-0.5 text-[11px] text-fg-muted">
              cierre: {conv.closing_tag}
            </span>
          )}
          {conv.order_id && (
            <span className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[11px] text-fg-muted">
              orden {conv.order_id}
            </span>
          )}
          <CandidateBadge conv={conv} />
        </div>
      </header>

      {/* Evolución del puntaje entre evals */}
      <section className="rounded-lg border border-line p-3">
        <div className="flex flex-wrap items-center gap-3">
          <EvalSparkline evals={conv.evals} threshold={threshold} />
          <div className="min-w-0 flex-1 text-sm">
            <p className={"font-medium " + trend.cls}>
              {trend.glyph}{" "}
              {conv.evals_count > 1
                ? trend.text +
                  (delta !== null
                    ? ` (${delta >= 0 ? "+" : ""}${delta.toFixed(2)} entre la primera y la última)`
                    : "")
                : "evaluada una sola vez (todavía sin historia)"}
            </p>
            <p className="mt-0.5 text-xs text-fg-muted">
              El mismo episodio se re-evalúa mientras la conversación sigue viva;
              cada punto es una corrida del eval.
            </p>
          </div>
        </div>
      </section>

      {/* Candidata a golden */}
      {(conv.is_candidate || conv.candidate_status) && (
        <section className="flex flex-wrap items-center gap-3 rounded-lg border border-orange/30 bg-orange/5 p-3 text-sm">
          <Icon.shield />
          <p className="min-w-0 flex-1 text-fg-soft">
            {conv.candidate_status === "approved"
              ? "Aprobada como golden: ya está en approved/ lista para mergear a curated.json."
              : conv.candidate_status === "pending"
                ? `Puntuó bajo el umbral (${threshold}) y el juez redactó un golden. Está esperando tu revisión.`
                : `Puntuó bajo el umbral (${threshold}) en su última eval — el draft del golden aún no está disponible.`}
          </p>
          {conv.candidate_status === "pending" && conv.candidate_id && (
            <button
              type="button"
              onClick={() => onOpenCandidate(conv.candidate_id)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-orange px-3 py-1.5 text-xs font-medium text-win-bg hover:opacity-90"
            >
              Revisar candidato <Icon.chevR />
            </button>
          )}
        </section>
      )}

      {/* Timeline de evaluaciones */}
      <section className="space-y-2">
        <h4 className="text-sm font-semibold text-fg-soft">
          Evaluaciones ({conv.evals_count})
        </h4>
        {evalsDesc.map((run, i) => (
          <EvalRunCard key={`${run.date}T${run.ts}`} run={run} latest={i === 0} />
        ))}
      </section>

      {/* Transcript del episodio */}
      <TranscriptSection sessionId={conv.session_id} episodeId={conv.episode_id} />
    </div>
  );
}

function TranscriptSection({
  sessionId,
  episodeId,
}: {
  sessionId: string;
  episodeId: string;
}) {
  const { data, isLoading, isError } = useEvalTranscript(sessionId, episodeId);

  return (
    <section className="space-y-2">
      <h4 className="text-sm font-semibold text-fg-soft">
        Conversación evaluada{" "}
        <span className="font-normal text-fg-faint">
          (lo que vio el juez, PII redactada)
        </span>
      </h4>
      {isLoading ? (
        <p className="text-sm text-fg-muted">Cargando conversación…</p>
      ) : isError || !data ? (
        <p className="text-sm text-red/80">
          No se pudo leer la conversación (puede haber rotado fuera del vault).
        </p>
      ) : (
        <div className="space-y-2 rounded-lg border border-line p-3">
          {data.truncated_at_human_takeover && (
            <p className="rounded bg-white/5 px-2 py-1 text-[11px] text-fg-muted">
              <Icon.info /> La evaluación corta donde un humano tomó el chat — lo
              posterior no se juzga al bot.
            </p>
          )}
          <div className="max-h-96 space-y-2 overflow-y-auto pr-1">
            {data.turns.map((t, i) => (
              <div
                key={i}
                className={
                  "max-w-[85%] rounded-lg px-3 py-1.5 text-sm " +
                  (t.role === "user"
                    ? "bg-white/5 text-fg-soft"
                    : "ml-auto bg-accent-soft text-fg")
                }
              >
                <span className="block text-[10px] uppercase tracking-wide text-fg-faint">
                  {t.role === "user" ? "Cliente" : "Asesor"}
                </span>
                {t.content}
                {t.tools.length > 0 && (
                  <span className="mt-1 block font-mono text-[10px] text-fg-faint">
                    tools: {t.tools.join(", ")}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

export default EpisodeEvals;
