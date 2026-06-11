import { useEffect, useState } from "react";

import {
  useApproveCandidate,
  useDiscardCandidate,
  useEvalCandidate,
  useEvalCandidates,
  type EvalCandidateSummary,
} from "@plugins/agents_admin/frontend/entities/eval-candidate";
import { Icon } from "@/shared/ui";

/**
 * Curación de goldens (Superficie 3 del eval loop).
 *
 * Acá llegan SOLO las conversaciones que puntuaron bajo el umbral en el eval
 * online: el juez redactó para cada una el `expected_outcome` ideal y quedaron
 * como candidatas. El operador revisa la conversación + scores, edita el
 * resultado esperado y decide:
 *   * **Aprobar** → el caso se mueve a `approved/` y al mergearse a
 *     `curated.json` se vuelve un test de regresión del CI (golden).
 *   * **Descartar** → no se convierte en golden (falso positivo / sin valor).
 *
 * `initialSelectedId` permite aterrizar con un candidato pre-seleccionado
 * (p.ej. desde la vista por-episodio, botón "Revisar candidato").
 */
export function GoldenEvalCuration({
  initialSelectedId = null,
}: {
  initialSelectedId?: string | null;
}) {
  const { data: candidates = [], isLoading, isError } = useEvalCandidates();
  const [selectedId, setSelectedId] = useState<string | null>(initialSelectedId);

  // Si el caller cambia el candidato a abrir (nuevo click en "Revisar"), seguirlo.
  useEffect(() => {
    if (initialSelectedId) setSelectedId(initialSelectedId);
  }, [initialSelectedId]);

  const selected = candidates.find((c) => c.id === selectedId) ?? null;

  return (
    <div className="flex h-full min-h-0 w-full text-fg">
      {/* Lista de candidatos */}
      <aside className="flex w-96 shrink-0 flex-col overflow-hidden border-r border-line">
        <header className="shrink-0 space-y-1 p-3 pb-2">
          <div className="flex items-center gap-2 px-1">
            <Icon.shield />
            <h2 className="text-sm font-semibold">Candidatos a golden</h2>
            <span className="ml-auto rounded-full bg-white/5 px-2 py-0.5 text-xs text-fg-muted">
              {candidates.length}
            </span>
          </div>
          <p className="px-1 text-[11px] leading-snug text-fg-faint">
            Conversaciones reales que puntuaron bajo el umbral. Aprobar una la
            convierte en golden: un caso de regresión que el CI corre siempre.
          </p>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-3 pt-1">
          {isLoading ? (
            <p className="px-2 text-sm text-fg-muted">Cargando…</p>
          ) : isError ? (
            <p className="px-2 text-sm text-red/80">
              No se pudieron leer los candidatos. Revisá el backend y reintentá.
            </p>
          ) : candidates.length === 0 ? (
            <p className="px-2 text-sm text-fg-muted">
              Sin candidatos pendientes. El harness los genera cuando una
              conversación real puntúa bajo el umbral.
            </p>
          ) : (
            <ul className="space-y-1">
              {candidates.map((c) => (
                <li key={c.id}>
                  <CandidateRow
                    candidate={c}
                    active={c.id === selectedId}
                    onClick={() => setSelectedId(c.id)}
                  />
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>

      {/* Detalle / revisión */}
      <section className="min-w-0 flex-1 overflow-y-auto p-4">
        {selected ? (
          <CandidatePane
            key={selected.id}
            summary={selected}
            onResolved={() => setSelectedId(null)}
          />
        ) : (
          <div className="flex h-full items-center justify-center px-8 text-center text-sm text-fg-faint">
            Elegí un candidato para revisarlo: vas a ver la conversación que
            falló, por qué falló cada métrica, y el resultado esperado que
            propone el juez (editalo antes de aprobar).
          </div>
        )}
      </section>
    </div>
  );
}

function scoreColor(avg: number | null): string {
  if (avg === null) return "text-fg-faint";
  if (avg >= 0.7) return "text-green";
  if (avg >= 0.5) return "text-orange";
  return "text-red";
}

function sessionLabel(sessionId: string): string {
  return sessionId.startsWith("wa_") ? sessionId.slice(3) : sessionId;
}

function CandidateRow({
  candidate,
  active,
  onClick,
}: {
  candidate: EvalCandidateSummary;
  active: boolean;
  onClick: () => void;
}) {
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
          {candidate.session_id ? sessionLabel(candidate.session_id) : candidate.id}
        </span>
        {candidate.episode_id && (
          <span className="shrink-0 rounded bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-fg-muted">
            {candidate.episode_id}
          </span>
        )}
        <span className={"ml-auto text-xs font-semibold " + scoreColor(candidate.avg_score)}>
          {candidate.avg_score === null ? "—" : candidate.avg_score.toFixed(2)}
        </span>
      </div>
      <p className="mt-0.5 truncate text-xs text-fg-muted">
        {candidate.num_turns} turnos
        {candidate.failed_metrics.length > 0
          ? ` · falla: ${candidate.failed_metrics.join(", ")}`
          : " · sin métricas falladas"}
      </p>
    </button>
  );
}

interface FailedMetricMeta {
  metric?: string;
  score?: number;
  reason?: string;
}

function CandidatePane({
  summary,
  onResolved,
}: {
  summary: EvalCandidateSummary;
  onResolved: () => void;
}) {
  const id = summary.id;
  const { data: detail, isLoading, isError } = useEvalCandidate(id);
  const approve = useApproveCandidate();
  const discard = useDiscardCandidate();
  const [outcome, setOutcome] = useState("");

  useEffect(() => {
    if (detail) setOutcome(detail.expected_outcome ?? "");
  }, [detail]);

  if (isLoading) {
    return <p className="text-sm text-fg-muted">Cargando candidato…</p>;
  }
  if (isError || !detail) {
    return (
      <p className="text-sm text-red/80">
        No se pudo leer el candidato (puede haber sido aprobado o descartado en
        otra pestaña).
      </p>
    );
  }

  const meta = detail.additional_metadata as Record<string, unknown>;
  const allScores = (meta.all_scores as { metric: string; score: number; success?: boolean }[]) ?? [];
  const failedMetrics = ((meta.failed_metrics as FailedMetricMeta[]) ?? []).filter(
    (f) => f && f.metric,
  );
  const busy = approve.isPending || discard.isPending;

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <header className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="font-mono text-sm text-fg-soft">
            {summary.session_id ? sessionLabel(summary.session_id) : id}
          </h3>
          {summary.episode_id && (
            <span className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[11px] text-fg-muted">
              {summary.episode_id}
            </span>
          )}
        </div>
        <p className="text-sm text-fg-muted">{detail.scenario}</p>
      </header>

      {/* Scores */}
      <div className="flex flex-wrap gap-2">
        {allScores.map((s) => (
          <span
            key={s.metric}
            className={
              "rounded-full px-2.5 py-1 text-xs font-medium " +
              (s.success ? "bg-green/15 text-green" : "bg-red/15 text-red")
            }
          >
            {s.metric}: {s.score.toFixed(2)}
          </span>
        ))}
      </div>

      {/* Por qué falló (razones del juez) */}
      {failedMetrics.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-semibold text-fg-soft">Por qué falló</h4>
          <ul className="space-y-2">
            {failedMetrics.map((f) => (
              <li key={f.metric} className="rounded-lg bg-red/5 p-2 text-xs">
                <span className="font-semibold text-red">
                  {f.metric}
                  {typeof f.score === "number" ? ` (${f.score.toFixed(2)})` : ""}
                </span>
                {f.reason && (
                  <p className="mt-0.5 whitespace-pre-wrap text-fg-muted">{f.reason}</p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Conversación (PII redactada) */}
      <details className="rounded-lg border border-line p-3" open>
        <summary className="cursor-pointer text-sm font-medium text-fg-soft">
          Conversación ({detail.turns.length} turnos, PII redactada)
        </summary>
        <div className="mt-3 max-h-72 space-y-2 overflow-y-auto pr-1">
          {detail.turns.map((t, i) => (
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
            </div>
          ))}
        </div>
      </details>

      {/* Expected outcome editable */}
      <div>
        <label className="mb-1 block text-sm font-medium text-fg-soft">
          Resultado esperado (redactado por el juez — editá antes de aprobar)
        </label>
        <textarea
          value={outcome}
          onChange={(e) => setOutcome(e.target.value)}
          rows={6}
          className="w-full rounded-lg border border-line-strong bg-white/5 p-3 text-sm text-fg focus:border-accent focus:outline-none"
        />
      </div>

      {/* Acciones */}
      <div className="flex items-center gap-3">
        <button
          disabled={busy}
          onClick={() =>
            approve.mutate({ id, expected_outcome: outcome }, { onSuccess: onResolved })
          }
          className="inline-flex items-center gap-1.5 rounded-lg bg-green px-4 py-2 text-sm font-medium text-win-bg hover:opacity-90 disabled:opacity-50"
        >
          <Icon.check /> Aprobar golden
        </button>
        <button
          disabled={busy}
          onClick={() => discard.mutate(id, { onSuccess: onResolved })}
          className="inline-flex items-center gap-1.5 rounded-lg border border-line-strong px-4 py-2 text-sm font-medium text-fg-soft hover:bg-white/5 disabled:opacity-50"
        >
          <Icon.trash /> Descartar
        </button>
        {(approve.isError || discard.isError) && (
          <span className="text-sm text-red">Error al guardar.</span>
        )}
      </div>
      <p className="text-[11px] leading-snug text-fg-faint">
        Aprobar mueve el caso a <code>approved/</code>; al mergearlo a{" "}
        <code>curated.json</code> queda como golden de regresión en CI. Descartar
        lo elimina del buffer.
      </p>
    </div>
  );
}

export default GoldenEvalCuration;
