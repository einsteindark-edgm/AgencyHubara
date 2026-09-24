/**
 * Modal del hilo de un turno (plan del laboratorio §11.1, diseño aprobado
 * 2026-09-23): encabezado con el turno, la ráfaga y un selector de bot;
 * asuntos y checks de ESE turno; el diagrama de secuencia (izquierda) y el
 * detalle del paso seleccionado (derecha); abajo, el resultado del turno.
 *
 * Los pasos vienen del contrato `lab@v1` (traza v2, o la v1 sintetizada sin
 * tiempos). Los asuntos y checks, de las evaluaciones del bot elegido.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { bogotaDayIsoFromMs, formatDayLabelEs, layoutSequence, type TraceStep } from "@/shared/lib";
import { Modal, SequenceTrace, TraceStepDetail } from "@/shared/ui";
import {
  apiErrorDetail,
  armLabel,
  Chip,
  useRunEvaluations,
  useTurnTrace,
  type EvalResult,
  type ThreadTurn,
} from "@plugins/lab/frontend/entities/lab-run";

interface Props {
  run: string;
  sid: string;
  turn: ThreadTurn;
  /** Bots que se pueden elegir en este turno. */
  arms: string[];
  initialArm: string;
  onClose: () => void;
}

const CLASSIFIER_LABEL: Record<string, string> = { B: "Jev", C: "OpenAI" };
const TITLE_ID = "lab-turn-trace-title";

function turnResults(results: EvalResult[], turn: number): EvalResult[] {
  return results.filter((r) => r.turn === turn && (r.verdict === "pasa" || r.verdict === "falla"));
}

export function TurnTraceModal({ run, sid, turn, arms, initialArm, onClose }: Props) {
  const [arm, setArm] = useState(initialArm);
  const [selected, setSelected] = useState(0);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  const trace = useTurnTrace({ run, sid, turnKey: turn.turn_key, arm, rep: 0 });
  const evals = useRunEvaluations(run, sid, arm);

  const steps = useMemo(() => (trace.data?.steps ?? []) as TraceStep[], [trace.data]);
  const layout = useMemo(() => layoutSequence(steps, { classifierLabel: CLASSIFIER_LABEL[arm] }), [steps, arm]);
  const row = layout.rows[Math.min(selected, Math.max(layout.rows.length - 1, 0))];

  const results = useMemo(() => {
    const episode = evals.data?.episodes.find((e) => e.episode_id === turn.episode_id);
    return episode ? turnResults(episode.results, turn.turn) : [];
  }, [evals.data, turn.episode_id, turn.turn]);
  const topics = results.flatMap((r) => r.topics);
  const failing = results.filter((r) => r.verdict === "falla");

  const n = turn.burst.length;
  const day = turn.at_ms !== null ? formatDayLabelEs(bogotaDayIsoFromMs(turn.at_ms)) : "";
  const time = turn.burst[n - 1]?.ts_ms ?? turn.at_ms;
  const meta = [
    day,
    time !== null ? new Intl.DateTimeFormat("es-CO", { timeZone: "America/Bogota", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date(time)) : "",
    n > 1 ? `ráfaga de ${n} mensajes` : "1 mensaje",
    arm === "A0" ? "producción" : "simulado",
  ]
    .filter(Boolean)
    .join(" · ");

  const traceError = trace.isError ? apiErrorDetail(trace.error) : null;

  return (
    <Modal open labelledBy={TITLE_ID} onClose={onClose}>
      <div className="grid gap-2.5 border-b border-line bg-titlebar px-4 py-3">
        <div className="flex flex-wrap items-start gap-3">
          <div>
            <p id={TITLE_ID} className="m-0 text-[15px] font-semibold">
              Hilo del turno {turn.turn}
            </p>
            <div className="text-xs tabular-nums text-fg-muted">{meta}</div>
          </div>
          <div role="group" aria-label="Bot del turno" className="ml-auto inline-flex flex-wrap rounded-lg border border-line bg-white/[0.06] p-0.5">
            {arms.map((a) => (
              <button
                key={a}
                type="button"
                aria-pressed={a === arm}
                onClick={() => {
                  setArm(a);
                  setSelected(0);
                }}
                className={
                  "rounded-md border-0 px-2.5 py-[7px] text-xs font-medium leading-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent " +
                  (a === arm ? "bg-accent-soft text-white" : "bg-transparent text-fg-muted")
                }
              >
                {armLabel(a)}
              </button>
            ))}
          </div>
          <button
            ref={closeRef}
            type="button"
            aria-label="Cerrar el hilo"
            onClick={onClose}
            className="h-8 w-8 flex-none rounded-lg border border-line bg-white/[0.06] text-base text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            ✕
          </button>
        </div>
        {topics.length > 0 ? (
          <div className="flex flex-wrap items-center gap-2">
            <span className="mr-0.5 text-[10px] font-semibold uppercase leading-none tracking-[0.08em] text-fg-faint">Asuntos</span>
            {topics.map((t, k) => (
              <Chip key={k} tone={t.covered ? "ok" : "bad"}>
                {`${t.topic}${t.msg !== null ? ` · mensaje ${t.msg}` : ""} · ${t.covered ? "respondido" : "sin responder"}`}
              </Chip>
            ))}
          </div>
        ) : null}
        {results.length > 0 ? (
          <div className="flex flex-wrap items-center gap-2">
            <span className="mr-0.5 text-[10px] font-semibold uppercase leading-none tracking-[0.08em] text-fg-faint">Checks del turno</span>
            {results.map((r) => (
              <Chip key={r.check_id} tone={r.verdict === "falla" ? "bad" : "ok"}>{`${r.check_id} · ${r.verdict}`}</Chip>
            ))}
          </div>
        ) : null}
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-auto min-[760px]:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)] min-[760px]:overflow-hidden">
        <div className="bg-canvas p-3 min-[760px]:overflow-auto min-[760px]:border-r min-[760px]:border-line min-[760px]:px-2 min-[760px]:pb-4 min-[760px]:pt-2">
          {trace.isPending ? <p className="p-3 text-[12.5px] text-fg-muted">Cargando la traza…</p> : null}
          {traceError ? <p className="p-3 text-[12.5px] text-fg-muted">{traceError.message ?? "No se pudo leer la traza de este turno."}</p> : null}
          {trace.isSuccess && layout.rows.length === 0 ? <p className="p-3 text-[12.5px] text-fg-muted">La traza de este turno no trae pasos.</p> : null}
          {trace.isSuccess && layout.rows.length > 0 ? <SequenceTrace layout={layout} selected={row?.index ?? 0} onSelect={setSelected} idPrefix="lab-seq" /> : null}
        </div>
        <div aria-live="polite" className="bg-inspector px-[18px] py-4 min-[760px]:overflow-auto">
          {trace.isSuccess && row ? <TraceStepDetail step={steps[row.stepIndex]} row={row} lanes={layout.lanes} /> : null}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-line bg-titlebar px-4 py-2.5 text-[12.5px]">
        {trace.data?.fidelity === "v1" ? (
          <>
            <Chip tone="neutral">v1</Chip>
            <span className="text-fg-soft">Traza v1: sin tiempos y con las guardas sin orden.</span>
          </>
        ) : null}
        {failing.length > 0 ? (
          <>
            <Chip tone="warn">⚠</Chip>
            <span>{`${failing.length} ${failing.length === 1 ? "check falla" : "checks fallan"} en este turno: ${failing.map((r) => r.check_id).join(", ")}.`}</span>
          </>
        ) : evals.isSuccess && results.length > 0 ? (
          <>
            <Chip tone="ok">✓</Chip>
            <span>Ningún check falla en este turno.</span>
          </>
        ) : null}
      </div>
    </Modal>
  );
}
