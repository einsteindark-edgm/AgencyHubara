/**
 * Ventana del hilo de un turno del laboratorio (plan §11.1): la de
 * `@/shared/ui` (`TurnWindow`, la misma de Calidad LLM de Agents) con el
 * selector de bot de la corrida. Los pasos vienen del contrato `lab@v1`
 * (traza v2, o la v1 sintetizada sin tiempos). Los checks, de las
 * evaluaciones del bot elegido y del registro.
 */

import { useMemo, useState } from "react";

import type { TraceStep } from "@/shared/lib";
import { TurnWindow } from "@/shared/ui";
import {
  apiErrorDetail,
  armLabel,
  engineDecisionsOf,
  useCheckCatalog,
  useRunEvaluations,
  useRunThread,
  useTurnTrace,
  type ThreadTurn,
} from "@plugins/lab/frontend/entities/lab-run";

import { turnReplies } from "../lib/thread-view";

interface Props {
  run: string;
  sid: string;
  turn: ThreadTurn;
  /** Bots que se pueden elegir en este turno. */
  arms: string[];
  initialArm: string;
  onClose: () => void;
}

export function TurnTraceModal({ run, sid, turn, arms, initialArm, onClose }: Props) {
  const [arm, setArm] = useState(initialArm);

  const trace = useTurnTrace({ run, sid, turnKey: turn.turn_key, arm, rep: 0 });
  const evals = useRunEvaluations(run, sid, arm);
  const catalog = useCheckCatalog();
  const thread = useRunThread(run, sid);

  const steps = useMemo(() => (trace.data?.steps ?? []) as TraceStep[], [trace.data]);
  const decisions = useMemo(() => (trace.data ? engineDecisionsOf(trace.data.trace) : []), [trace.data]);
  const replies = useMemo(() => (thread.data ? turnReplies(thread.data, turn, arm) : []), [thread.data, turn, arm]);
  const results = useMemo(() => {
    const episode = evals.data?.episodes.find((e) => e.episode_id === turn.episode_id);
    return episode ? episode.results.filter((r) => r.turn === turn.turn) : [];
  }, [evals.data, turn.episode_id, turn.turn]);

  const armSwitch = (
    <div role="group" aria-label="Bot del turno" className="inline-flex flex-wrap rounded-lg border border-line bg-white/[0.06] p-0.5">
      {arms.map((a) => (
        <button
          key={a}
          type="button"
          aria-pressed={a === arm}
          onClick={() => setArm(a)}
          className={
            "rounded-md border-0 px-2.5 py-[7px] text-xs font-medium leading-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent " +
            (a === arm ? "bg-accent-soft text-white" : "bg-transparent text-fg-muted")
          }
        >
          {armLabel(a)}
        </button>
      ))}
    </div>
  );

  return (
    <TurnWindow
      idPrefix="lab-turn"
      turn={turn}
      botLabel={armLabel(arm)}
      headerExtra={armSwitch}
      resetKey={arm}
      replies={replies}
      repliesPending={thread.isPending}
      evaluation={{ pending: evals.isPending, error: evals.isError, results }}
      catalog={catalog.data}
      trace={{
        pending: trace.isPending,
        success: trace.isSuccess,
        errorStatus: trace.isError ? (apiErrorDetail(trace.error).status ?? 0) : null,
        fidelity: trace.data?.fidelity ?? null,
        steps,
        decisions,
      }}
      onClose={onClose}
    />
  );
}
