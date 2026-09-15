import { useState } from "react";

import {
  useCreateLabel,
  useEvalLabels,
  type HumanVerdict,
} from "@plugins/agents_admin/frontend/entities/eval-label";
import {
  failurePointLabel,
  fidelityLabel,
  formatCompliance,
  groupResultsByFamily,
  levelLabel,
  stageColor,
  statusColor,
  statusGlyph,
  statusLabel,
  useRescoreScorecard,
  type CheckDefinition,
  type CheckRegistry,
  type CheckResult,
  type CheckStatus,
  type EpisodeRef,
  type EpisodeVerdict,
  type Fidelity,
  type ScorecardDetail,
  type VerdictCounts,
} from "@plugins/agents_admin/frontend/entities/scorecard";
import { Icon } from "@/shared/ui";

interface Props {
  episode: EpisodeRef;
  detail: ScorecardDetail;
  registry: CheckRegistry | undefined;
  selectedCheckId: string | null;
  onSelectCheck: (checkId: string) => void;
}

const VERDICT_TEXT: Record<EpisodeVerdict, string> = {
  FALLA: "text-red",
  ALERTA: "text-orange",
  PASA: "text-green",
  SIN_DATOS: "text-fg-muted",
};

const FIDELITY_CLASS: Record<Fidelity, string> = {
  trace: "bg-ok-soft text-ok",
  legacy: "bg-warn-soft text-warn",
  empty: "bg-neutral-soft text-fg-muted",
};

const COUNT_PILLS: ReadonlyArray<{
  key: keyof VerdictCounts;
  one: string;
  many: string;
  className: string;
}> = [
  { key: "critico", one: "crítico", many: "críticos", className: "bg-danger-soft text-red font-semibold" },
  { key: "mayor", one: "mayor", many: "mayores", className: "bg-warn-soft text-orange font-semibold" },
  { key: "menor", one: "menor", many: "menores", className: "bg-yellow/15 text-yellow font-semibold" },
  { key: "pasa", one: "pasa", many: "pasan", className: "bg-white/5 text-fg-muted" },
  { key: "no_aplica", one: "no aplica", many: "no aplican", className: "bg-white/5 text-fg-muted" },
  { key: "desconocido", one: "desconocido", many: "desconocidos", className: "bg-violet-soft text-violet" },
];

function StatusDot({ status }: { status: CheckStatus }) {
  return (
    <span
      className="inline-grid h-4 w-4 shrink-0 place-items-center rounded-full text-[10px] font-bold text-win-bg"
      style={{ background: statusColor(status) }}
      aria-hidden="true"
    >
      {statusGlyph(status)}
    </span>
  );
}

function RescoreControls({ episode, defaultJudge }: { episode: EpisodeRef; defaultJudge: boolean }) {
  const [judge, setJudge] = useState(defaultJudge);
  const rescore = useRescoreScorecard();
  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        onClick={() =>
          rescore.mutate({ session_id: episode.sessionId, episode_id: episode.episodeId, judge })
        }
        disabled={rescore.isPending}
        className="inline-flex items-center gap-1.5 rounded-md border border-line-strong px-2.5 py-1 text-xs font-medium text-fg transition hover:bg-white/5 disabled:opacity-50"
      >
        <Icon.refresh /> {rescore.isPending ? "Recalculando…" : "Recalcular"}
      </button>
      <label className="inline-flex items-center gap-1.5 text-xs text-fg-muted">
        <input type="checkbox" checked={judge} onChange={(e) => setJudge(e.target.checked)} className="accent-accent" />
        con juez
      </label>
      {rescore.isError && (
        <span className="text-xs text-red" role="alert">
          No se pudo recalcular: {rescore.error.message}
        </span>
      )}
    </div>
  );
}

function LabelBox({ episode, checkId }: { episode: EpisodeRef; checkId: string }) {
  const [note, setNote] = useState("");
  const labels = useEvalLabels(episode.sessionId, episode.episodeId);
  const create = useCreateLabel();
  const mine = (labels.data?.labels ?? []).filter((l) => l.check_id === checkId);
  const savedHere = create.isSuccess && create.variables?.check_id === checkId;
  const failedHere = create.isError && create.variables?.check_id === checkId;

  const submit = (verdict: HumanVerdict) =>
    create.mutate({
      session_id: episode.sessionId,
      episode_id: episode.episodeId,
      check_id: checkId,
      verdict,
      note: note.trim(),
    });

  return (
    <div className="mt-3 rounded-md border border-line p-2">
      <div className="mb-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-fg-faint">
        <Icon.tag /> Etiquetar (calibración del juez)
      </div>
      {mine.length > 0 && (
        <ul className="mb-2 space-y-1">
          {mine.map((l) => (
            <li key={`${l.labeled_at}-${l.verdict}`} className="text-xs text-fg-muted">
              <span className={l.verdict === "falla" ? "font-semibold text-red" : "font-semibold text-green"}>
                {l.verdict === "falla" ? "✗ falla" : l.verdict === "pasa" ? "✓ pasa" : l.verdict}
              </span>
              {l.note ? ` · ${l.note}` : ""}
              {l.labeled_at ? <span className="text-fg-faint"> · {l.labeled_at.slice(0, 16).replace("T", " ")}</span> : null}
            </li>
          ))}
        </ul>
      )}
      <label className="sr-only" htmlFor={`label-note-${checkId}`}>
        Nota de la etiqueta
      </label>
      <textarea
        id={`label-note-${checkId}`}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        rows={2}
        placeholder="Nota (opcional): por qué pasa o falla"
        className="w-full resize-y rounded-md border border-line bg-white/5 p-1.5 text-xs text-fg placeholder:text-fg-faint"
      />
      <div className="mt-1.5 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => submit("pasa")}
          disabled={create.isPending}
          className="rounded-md border border-line-strong px-2.5 py-1 text-xs font-medium text-green hover:bg-white/5 disabled:opacity-50"
        >
          ✓ Marcar pasa
        </button>
        <button
          type="button"
          onClick={() => submit("falla")}
          disabled={create.isPending}
          className="rounded-md border border-line-strong px-2.5 py-1 text-xs font-medium text-red hover:bg-white/5 disabled:opacity-50"
        >
          ✗ Marcar falla
        </button>
        {savedHere && <span className="text-xs text-green">Etiqueta guardada</span>}
        {failedHere && (
          <span className="text-xs text-red" role="alert">
            No se pudo guardar: {create.error?.message}
          </span>
        )}
      </div>
    </div>
  );
}

function CheckDetail({
  episode,
  checkId,
  check,
  result,
  status,
}: {
  episode: EpisodeRef;
  checkId: string;
  check: CheckDefinition | null;
  result: CheckResult | null;
  status: CheckStatus;
}) {
  const kind = (result?.source ?? check?.kind) === "judge" ? "juez" : "código";
  const turn = result?.turn;
  return (
    <section aria-label={`Detalle de ${checkId}`} className="text-sm">
      <div className="flex flex-wrap items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-fg-faint">
        <StatusDot status={status} />
        <span className="font-mono normal-case text-fg-muted">{checkId}</span>· {kind} · {statusLabel(status)}
        {turn != null ? ` · turno ${turn}` : ""}
      </div>
      <p className="mt-1 font-semibold text-fg">{check?.name ?? checkId}</p>
      {check?.rule && (
        <>
          <h5 className="mt-2 text-[10px] font-semibold uppercase tracking-wide text-fg-faint">Regla</h5>
          <p className="text-fg-soft">{check.rule}</p>
        </>
      )}
      {check?.applies && (
        <>
          <h5 className="mt-2 text-[10px] font-semibold uppercase tracking-wide text-fg-faint">Aplica cuando</h5>
          <p className="text-fg-soft">{check.applies}</p>
        </>
      )}
      {result?.evidence && (
        <>
          <h5 className="mt-2 text-[10px] font-semibold uppercase tracking-wide text-fg-faint">Evidencia</h5>
          <blockquote className="rounded-r-md border-l-2 border-accent bg-white/5 px-2 py-1 text-xs text-fg-soft">
            {result.evidence}
          </blockquote>
        </>
      )}
      {result?.critique && (
        <>
          <h5 className="mt-2 text-[10px] font-semibold uppercase tracking-wide text-fg-faint">Crítica del juez</h5>
          <p className="text-fg-soft">{result.critique}</p>
        </>
      )}
      {!result && (
        <p className="mt-2 text-xs text-fg-muted">
          Sin resultado en este scorecard{check?.kind === "judge" ? ": recalcula con juez para evaluarlo." : "."}
        </p>
      )}
      {(check?.origin.length ?? 0) > 0 && (
        <>
          <h5 className="mt-2 text-[10px] font-semibold uppercase tracking-wide text-fg-faint">Origen</h5>
          <ul className="flex flex-wrap gap-1">
            {check!.origin.map((o) => (
              <li key={o} className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-fg-muted">
                {o}
              </li>
            ))}
          </ul>
        </>
      )}
      {check?.twin_of && (
        <p className="mt-2 text-xs text-fg-muted">
          Gemelo de <span className="font-mono">{check.twin_of}</span>: mide si la guarda de runtime tuvo que actuar.
        </p>
      )}
      <LabelBox episode={episode} checkId={checkId} />
    </section>
  );
}

/**
 * Panel del scorecard de un episodio: veredicto (nunca un promedio), primer
 * fallo y primer crítico, conteos por nivel, fidelidad de la traza, la lista de
 * checks por familia y el detalle del check seleccionado con recálculo y
 * etiquetado humano. La selección vive en el padre (compartida con la tira).
 */
export function ScorecardPanel({ episode, detail, registry, selectedCheckId, onSelectCheck }: Props) {
  const sc = detail.scorecard;

  if (!sc) {
    return (
      <aside aria-label="Scorecard del episodio" className="space-y-3 rounded-lg border border-line p-3 text-sm">
        <p className="text-fg-muted">
          Este episodio aún no tiene scorecard. Recalcula para generarlo a partir de su trayectoria.
        </p>
        <RescoreControls episode={episode} defaultJudge={false} />
      </aside>
    );
  }

  const reg = registry ?? { registry_version: 0, stages: [], families: [], checks: [] };
  const groups = groupResultsByFamily(reg, sc.results);
  const selectedItem = selectedCheckId
    ? groups.flatMap((g) => g.items).find((i) => i.checkId === selectedCheckId)
    : undefined;
  const ff = failurePointLabel(sc.first_failure);
  const fc = failurePointLabel(sc.first_critical);

  return (
    <aside aria-label="Scorecard del episodio" className="flex min-w-0 flex-col gap-3 rounded-lg border border-line p-3">
      <header>
        <div className="text-[10px] font-semibold uppercase tracking-wider text-fg-faint">Veredicto del episodio</div>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className={"text-3xl font-bold leading-none tracking-tight " + VERDICT_TEXT[sc.verdict]}>
            {sc.verdict === "SIN_DATOS" ? "SIN DATOS" : sc.verdict}
          </span>
          <span className="text-xs leading-snug text-fg-muted">
            {ff ? <span className="block">primer fallo · {ff}</span> : <span className="block">sin fallos</span>}
            {fc && <span className="block">primer crítico · {fc}</span>}
          </span>
          <span className={"ml-auto rounded-full px-2 py-0.5 text-[11px] font-medium " + FIDELITY_CLASS[sc.fidelity]}>
            {fidelityLabel(sc.fidelity)}
          </span>
        </div>
        <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Conteo de checks">
          {COUNT_PILLS.map((p) => {
            const n = sc.counts[p.key];
            return (
              <li key={p.key} className={"rounded-full px-2 py-0.5 text-[11px] " + p.className}>
                {`${n} ${n === 1 ? p.one : p.many}`}
              </li>
            );
          })}
          <li
            className="rounded-full border border-line px-2 py-0.5 text-[11px] text-fg-muted"
            title="Checks que pasan sobre los aplicables. Secundario: el veredicto lo deciden los niveles."
          >
            cumplimiento {formatCompliance(sc.compliance)}
          </li>
          {detail.legacy && (
            <li
              className="rounded-full border border-line px-2 py-0.5 text-[11px] text-fg-faint line-through"
              title={`Promedio del eval anterior (${Object.keys(detail.legacy.metrics).length} métricas, ${detail.legacy.date}). Solo referencia: un promedio esconde fallos críticos.`}
            >
              puntaje legado {detail.legacy.avg.toFixed(2)}
            </li>
          )}
        </ul>
        <div className="mt-2">
          <RescoreControls key={`${episode.sessionId}::${episode.episodeId}`} episode={episode} defaultJudge={sc.judge} />
        </div>
      </header>

      <div className="border-t border-line pt-2">
        {selectedCheckId ? (
          <CheckDetail
            key={selectedCheckId}
            episode={episode}
            checkId={selectedCheckId}
            check={selectedItem?.check ?? reg.checks.find((c) => c.id === selectedCheckId) ?? null}
            result={selectedItem?.result ?? null}
            status={selectedItem?.status ?? "sin_resultado"}
          />
        ) : (
          <p className="text-xs text-fg-muted">
            Elige un check en la tira o en la lista para ver la regla, la evidencia y la crítica.
          </p>
        )}
      </div>

      <div className="max-h-[26rem] overflow-y-auto border-t border-line pt-1">
        {groups.map((g) => {
          const fails = g.items.filter((i) => i.result?.verdict === "falla").length;
          return (
            <section key={g.id} className="mt-2">
              <h4 className="mb-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-fg-faint">
                <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: stageColor(g.stage) }} aria-hidden="true" />
                {g.label}
                {fails > 0 && <span className="normal-case tracking-normal text-red">· {fails} con falla</span>}
              </h4>
              <ul>
                {g.items.map((item) => {
                  const selected = item.checkId === selectedCheckId;
                  const dim = item.status === "no_aplica" || item.status === "sin_resultado";
                  const level = item.result?.level ?? item.check?.level ?? "menor";
                  const turn = item.result?.turn;
                  return (
                    <li key={item.checkId}>
                      <button
                        type="button"
                        aria-pressed={selected}
                        aria-label={`${item.checkId} ${item.check?.name ?? ""} · ${statusLabel(item.status)} · ${levelLabel(level)}${turn != null ? ` · turno ${turn}` : ""}`}
                        onClick={() => onSelectCheck(item.checkId)}
                        className={
                          "grid w-full grid-cols-[1rem_4.2rem_minmax(0,1fr)_auto] items-center gap-2 rounded-md px-1.5 py-1 text-left text-xs transition hover:bg-white/5 " +
                          (selected ? "bg-accent-soft" : "")
                        }
                      >
                        <StatusDot status={item.status} />
                        <span className={"font-mono text-[11px] " + (dim ? "text-fg-faint" : "text-fg-muted")}>{item.checkId}</span>
                        <span className={"truncate " + (dim ? "text-fg-faint" : "text-fg")}>{item.check?.name ?? item.checkId}</span>
                        <span className="whitespace-nowrap text-[10px] uppercase tracking-wide text-fg-faint">
                          {item.status === "pasa" || item.status === "desconocido"
                            ? `${levelLabel(level)} · ${statusLabel(item.status)}`
                            : dim
                              ? statusLabel(item.status)
                              : levelLabel(level)}
                          {turn != null ? ` · t${turn}` : ""}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          );
        })}
      </div>
    </aside>
  );
}

export default ScorecardPanel;
