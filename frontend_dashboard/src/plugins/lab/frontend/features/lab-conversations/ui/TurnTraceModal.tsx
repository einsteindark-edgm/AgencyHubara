/**
 * Modal del hilo de un turno (plan del laboratorio §11.1; revisión del
 * 2026-09-29: el operador no entendía los códigos ni la jerga). Encabezado con
 * el turno y un selector de bot; tres pestañas:
 *  - Resultado (abre primero): lo que escribió el cliente, lo que respondió
 *    ESE bot y cómo le fue — los checks por su nombre, lo que falló primero
 *    con el porqué y lo que se esperaba — y los asuntos del cliente.
 *  - Paso a paso: el diagrama de secuencia (izquierda) y el detalle del paso
 *    seleccionado (derecha), en palabras del operador.
 *  - Decisiones de Jev (solo con el bot nuevo): quién decidió cada cosa.
 *
 * Los pasos vienen del contrato `lab@v1` (traza v2, o la v1 sintetizada sin
 * tiempos). Los checks, de las evaluaciones del bot elegido y del registro.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { bogotaDayIsoFromMs, formatDayLabelEs, layoutSequence, type TraceStep } from "@/shared/lib";
import { Modal, SequenceTrace, TraceStepDetail } from "@/shared/ui";
import {
  apiErrorDetail,
  armLabel,
  Chip,
  checksHeadline,
  engineDecisionsOf,
  turnVerdict,
  useCheckCatalog,
  useRunEvaluations,
  useRunThread,
  useTurnTrace,
  VerdictBadge,
  type EvalResult,
  type ThreadTurn,
} from "@plugins/lab/frontend/entities/lab-run";

import { turnReplies } from "../lib/thread-view";
import { CheckResults } from "./CheckResults";
import { EngineDecisions } from "./EngineDecisions";

interface Props {
  run: string;
  sid: string;
  turn: ThreadTurn;
  /** Bots que se pueden elegir en este turno. */
  arms: string[];
  initialArm: string;
  onClose: () => void;
}

type Tab = "resultado" | "pasos" | "jev";

const TITLE_ID = "lab-turn-trace-title";
const EYEBROW = "mb-1.5 text-[10px] font-semibold uppercase leading-none tracking-[0.08em] text-fg-faint";

function turnResults(results: EvalResult[], turn: number): EvalResult[] {
  return results.filter((r) => r.turn === turn);
}

export function TurnTraceModal({ run, sid, turn, arms, initialArm, onClose }: Props) {
  const [arm, setArm] = useState(initialArm);
  const [tab, setTab] = useState<Tab>("resultado");
  const [selected, setSelected] = useState(0);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  const trace = useTurnTrace({ run, sid, turnKey: turn.turn_key, arm, rep: 0 });
  const evals = useRunEvaluations(run, sid, arm);
  const catalog = useCheckCatalog();
  const thread = useRunThread(run, sid);

  const steps = useMemo(() => (trace.data?.steps ?? []) as TraceStep[], [trace.data]);
  const decisions = useMemo(() => (trace.data ? engineDecisionsOf(trace.data.trace) : []), [trace.data]);
  const layout = useMemo(() => layoutSequence(steps), [steps]);
  const row = layout.rows[Math.min(selected, Math.max(layout.rows.length - 1, 0))];
  const replies = useMemo(() => (thread.data ? turnReplies(thread.data, turn, arm) : []), [thread.data, turn, arm]);

  const results = useMemo(() => {
    const episode = evals.data?.episodes.find((e) => e.episode_id === turn.episode_id);
    return episode ? turnResults(episode.results, turn.turn) : [];
  }, [evals.data, turn.episode_id, turn.turn]);
  const topics = results.flatMap((r) => r.topics);
  const failing = results.filter((r) => r.verdict === "falla").length;
  const decided = results.filter((r) => r.verdict === "falla" || r.verdict === "pasa").length;

  const n = turn.burst.length;
  const day = turn.at_ms !== null ? formatDayLabelEs(bogotaDayIsoFromMs(turn.at_ms)) : "";
  const time = turn.burst[n - 1]?.ts_ms ?? turn.at_ms;
  const meta = [
    day,
    time !== null ? new Intl.DateTimeFormat("es-CO", { timeZone: "America/Bogota", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(time)) : "",
    n > 1 ? `el cliente escribió ${n} mensajes seguidos` : "1 mensaje del cliente",
  ]
    .filter(Boolean)
    .join(" · ");

  const tabs: Array<[Tab, string]> = [
    ["resultado", "Resultado"],
    ["pasos", "Paso a paso"],
    ...(decisions.length > 0 ? ([["jev", "Decisiones de Jev"]] as Array<[Tab, string]>) : []),
  ];
  const shownTab: Tab = tab === "jev" && decisions.length === 0 ? "resultado" : tab;
  const traceError = trace.isError ? apiErrorDetail(trace.error) : null;

  return (
    <Modal open labelledBy={TITLE_ID} onClose={onClose}>
      <div className="grid gap-2 border-b border-line bg-titlebar px-4 pt-3">
        <div className="flex flex-wrap items-start gap-3">
          <div>
            <p id={TITLE_ID} className="m-0 text-[15px] font-semibold">
              Hilo del turno {turn.turn}
            </p>
            <div className="text-xs tabular-nums text-fg-muted">{meta}</div>
            {results.length > 0 ? (
              <div role="group" aria-label="Resultado de este bot en el turno" className="mt-1.5 flex flex-wrap items-center gap-2 text-[12.5px]">
                <VerdictBadge verdict={turnVerdict(catalog.data, results)} />
                <span className="font-semibold">{checksHeadline(failing, decided)}</span>
              </div>
            ) : null}
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
        <div role="tablist" aria-label="Qué ver del turno" className="flex gap-0.5">
          {tabs.map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              id={`lab-turn-tab-${key}`}
              aria-selected={shownTab === key}
              aria-controls="lab-turn-panel"
              onClick={() => setTab(key)}
              className={
                "border-0 border-b-2 bg-transparent px-2.5 py-2 text-[12.5px] font-medium leading-none focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent " +
                (shownTab === key ? "border-accent text-fg" : "border-transparent text-fg-muted")
              }
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div role="tabpanel" id="lab-turn-panel" aria-labelledby={`lab-turn-tab-${shownTab}`} className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {shownTab === "resultado" ? (
          <div className="grid min-h-0 flex-1 content-start gap-4 overflow-auto bg-canvas px-4 py-3.5">
            <section aria-label="Lo que escribió el cliente">
              <h3 className={EYEBROW}>Lo que escribió el cliente</h3>
              <div className="flex flex-col gap-1.5">
                {turn.burst.map((m, k) => (
                  <p key={k} className="m-0 max-w-[82%] self-start whitespace-pre-line break-words rounded-[14px] rounded-bl-[5px] bg-bubble-out px-3 py-2 text-[13.5px] leading-[1.42] text-fg">
                    {m.text}
                  </p>
                ))}
              </div>
            </section>

            <section aria-label={`Lo que respondió ${armLabel(arm)}`}>
              <h3 className={EYEBROW}>{`Lo que respondió ${armLabel(arm)}`}</h3>
              {thread.isPending ? <p className="m-0 text-[12.5px] text-fg-muted">Cargando la respuesta…</p> : null}
              <div className="flex flex-col gap-1.5">
                {replies.map((r, k) =>
                  r.dir === "note" || r.dir === "system" ? (
                    <p key={k} className="m-0 self-end text-[12px] italic text-fg-muted">{r.text}</p>
                  ) : (
                    <p
                      key={k}
                      className={
                        "m-0 max-w-[82%] self-end whitespace-pre-line break-words rounded-[14px] px-3 py-2 text-[13.5px] leading-[1.42] " +
                        (r.dir === "comp" ? "border border-info/40 bg-info-soft text-white" : "rounded-br-[5px] bg-bubble-in text-white") +
                        (r.byHuman ? " ring-2 ring-warn/60" : "") +
                        (r.complement ? " border border-dashed border-white/50" : "")
                      }
                    >
                      {r.byHuman ? <span className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.06em] text-white/80">Persona del equipo</span> : null}
                      {r.complement ? <span className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.06em] text-white/80">Mensaje de complemento</span> : null}
                      {r.text || (r.hasImage ? "📷 Foto" : "")}
                    </p>
                  ),
                )}
              </div>
            </section>

            <section aria-label="Cómo le fue en este turno">
              <h3 className={EYEBROW}>Cómo le fue en este turno</h3>
              {evals.isPending ? <p className="m-0 text-[12.5px] text-fg-muted">Cargando la evaluación…</p> : null}
              {evals.isError ? <p className="m-0 text-[12.5px] text-fg-muted">No se pudo leer la evaluación de este bot.</p> : null}
              {evals.isSuccess && results.length === 0 ? (
                <p className="m-0 text-[12.5px] text-fg-muted">Este bot todavía no tiene evaluación en este turno.</p>
              ) : null}
              {results.length > 0 ? <CheckResults results={results} catalog={catalog.data} /> : null}
            </section>

            {topics.length > 0 ? (
              <section aria-label="Asuntos del cliente">
                <h3 className={EYEBROW}>Asuntos del cliente</h3>
                <ul className="m-0 grid list-none gap-1.5 p-0 text-[12.5px]">
                  {topics.map((t, k) => (
                    <li key={k} className="flex flex-wrap items-center gap-2">
                      <span className="text-fg">{t.topic}</span>
                      {t.msg !== null ? <span className="text-[11px] text-fg-faint">{`mensaje ${t.msg}`}</span> : null}
                      <Chip tone={t.covered ? "ok" : "bad"}>{t.covered ? "respondido" : "sin responder"}</Chip>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </div>
        ) : null}

        {shownTab === "pasos" ? (
          <div className="flex min-h-0 flex-1 flex-col">
            {trace.data?.fidelity === "v1" ? (
              <p className="m-0 border-b border-line bg-titlebar px-4 py-2 text-[12px] text-fg-muted">
                Este turno se registró con el formato anterior: no trae los tiempos de cada paso.
              </p>
            ) : null}
            <div className="grid min-h-0 flex-1 grid-cols-1 overflow-auto min-[760px]:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)] min-[760px]:overflow-hidden">
              <div className="bg-canvas p-3 min-[760px]:overflow-auto min-[760px]:border-r min-[760px]:border-line min-[760px]:px-2 min-[760px]:pb-4 min-[760px]:pt-2">
                {trace.isPending ? <p className="p-3 text-[12.5px] text-fg-muted">Cargando el paso a paso…</p> : null}
                {traceError ? (
                  <p className="p-3 text-[12.5px] text-fg-muted">
                    {traceError.status === 404 ? "Este bot no tiene el paso a paso de este turno." : "No se pudo leer el paso a paso de este turno."}
                  </p>
                ) : null}
                {trace.isSuccess && layout.rows.length === 0 ? <p className="p-3 text-[12.5px] text-fg-muted">Este turno no trae pasos.</p> : null}
                {trace.isSuccess && layout.rows.length > 0 ? <SequenceTrace layout={layout} selected={row?.index ?? 0} onSelect={setSelected} idPrefix="lab-seq" /> : null}
              </div>
              <div aria-live="polite" className="bg-inspector px-[18px] py-4 min-[760px]:overflow-auto">
                {trace.isSuccess && row ? <TraceStepDetail step={steps[row.stepIndex]} row={row} lanes={layout.lanes} steps={steps} /> : null}
              </div>
            </div>
          </div>
        ) : null}

        {shownTab === "jev" ? (
          <div className="min-h-0 flex-1 overflow-auto">
            <EngineDecisions decisions={decisions} />
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
