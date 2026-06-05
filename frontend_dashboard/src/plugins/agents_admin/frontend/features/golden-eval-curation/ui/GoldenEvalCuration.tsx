import { useEffect, useState } from "react";

import {
  useApproveCandidate,
  useDiscardCandidate,
  useEvalCandidate,
  useEvalCandidates,
  type EvalCandidateSummary,
} from "@/entities/eval-candidate";
import { Icon } from "@/shared/ui";

/**
 * Pestaña de curación de goldens (Superficie 3 del eval loop).
 *
 * Lista los candidatos a golden auto-curados por el juez, permite revisar la
 * conversación + scores, editar el `expected_outcome` propuesto, y aprobar
 * (→ `approved/`, el operador lo mergea a curated.json) o descartar.
 */
export function GoldenEvalCuration() {
  const { data: candidates = [], isLoading } = useEvalCandidates();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const selected = candidates.find((c) => c.id === selectedId) ?? null;

  return (
    <div className="flex h-full min-h-0 w-full text-fg">
      {/* Lista de candidatos */}
      <aside className="w-80 shrink-0 overflow-y-auto border-r border-line p-3">
        <header className="mb-3 flex items-center gap-2 px-1">
          <Icon.shield />
          <h2 className="text-sm font-semibold">Candidatos a golden</h2>
          <span className="ml-auto rounded-full bg-white/5 px-2 py-0.5 text-xs text-fg-muted">
            {candidates.length}
          </span>
        </header>
        {isLoading ? (
          <p className="px-2 text-sm text-fg-muted">Cargando…</p>
        ) : candidates.length === 0 ? (
          <p className="px-2 text-sm text-fg-muted">
            Sin candidatos pendientes. El harness los genera cuando una
            conversación real puntúa bajo.
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
      </aside>

      {/* Detalle / revisión */}
      <section className="min-w-0 flex-1 overflow-y-auto p-4">
        {selected ? (
          <CandidatePane
            key={selected.id}
            id={selected.id}
            onResolved={() => setSelectedId(null)}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-fg-faint">
            Elegí un candidato para revisarlo.
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
          {candidate.id}
        </span>
        <span className={"ml-auto text-xs font-semibold " + scoreColor(candidate.avg_score)}>
          {candidate.avg_score === null ? "—" : candidate.avg_score.toFixed(2)}
        </span>
      </div>
      <p className="mt-0.5 truncate text-xs text-fg-muted">
        {candidate.num_turns} turnos · {candidate.failed_metrics.length} métricas falladas
      </p>
    </button>
  );
}

function CandidatePane({ id, onResolved }: { id: string; onResolved: () => void }) {
  const { data: detail, isLoading } = useEvalCandidate(id);
  const approve = useApproveCandidate();
  const discard = useDiscardCandidate();
  const [outcome, setOutcome] = useState("");

  useEffect(() => {
    if (detail) setOutcome(detail.expected_outcome ?? "");
  }, [detail]);

  if (isLoading || !detail) {
    return <p className="text-sm text-fg-muted">Cargando candidato…</p>;
  }

  const meta = detail.additional_metadata as Record<string, unknown>;
  const allScores = (meta.all_scores as { metric: string; score: number; success?: boolean }[]) ?? [];
  const busy = approve.isPending || discard.isPending;

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <header>
        <h3 className="font-mono text-sm text-fg-soft">{id}</h3>
        <p className="mt-1 text-sm text-fg-muted">{detail.scenario}</p>
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

      {/* Conversación (PII redactada) */}
      <details className="rounded-lg border border-line p-3">
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
    </div>
  );
}
