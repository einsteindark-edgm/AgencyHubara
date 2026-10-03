/**
 * La ventana del hilo de un turno (laboratorio §11.1 y Calidad LLM de Agents,
 * 2026-10-02; revisión del 2026-09-29: el operador no entendía los códigos ni
 * la jerga). Encabezado con el turno, su resultado y un hueco para lo que
 * agrega cada pantalla (el selector de bot del laboratorio); tres pestañas:
 *  - Resultado (abre primero): lo que escribió el cliente, lo que respondió el
 *    bot y cómo le fue — los checks por su nombre, lo que falló primero con el
 *    porqué y lo que se esperaba — y los asuntos del cliente.
 *  - Paso a paso: el diagrama de secuencia (izquierda) y el detalle del paso
 *    seleccionado (derecha), en palabras del operador.
 *  - Decisiones de Jev (solo si las hubo): quién decidió cada cosa.
 *
 * Presentacional: cada pantalla trae sus datos (la corrida del laboratorio o
 * la conversación real) y se los pasa.
 */

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import {
  bogotaDayIsoFromMs,
  checksHeadline,
  formatDayLabelEs,
  hhmm,
  layoutSequence,
  turnVerdict,
  type CheckCatalogView,
  type EngineDecisionView,
  type EvalResultView,
  type ReplyItem,
  type ThreadTurnView,
  type TraceStep,
} from "@/shared/lib";

import { Modal } from "../Modal";
import { SequenceTrace } from "../SequenceTrace";
import { TraceStepDetail } from "../TraceStepDetail";
import { CheckResults } from "./CheckResults";
import { ReplyBubbles } from "./ConversationThread";
import { EngineDecisions } from "./EngineDecisions";
import { Chip, VerdictBadge } from "./QualityChips";

export interface TurnWindowProps {
  turn: ThreadTurnView;
  /** Quién respondió, en palabras («Producción», «Bot nuevo con Jev»…). */
  botLabel: string;
  /** Lo que agrega cada pantalla junto al título (el selector de bot del laboratorio). */
  headerExtra?: ReactNode;
  /** Cambia cuando cambia lo que se muestra (otro bot): vuelve al primer paso. */
  resetKey?: string;
  replies: ReplyItem[];
  repliesPending: boolean;
  evaluation: { pending: boolean; error: boolean; results: EvalResultView[] };
  catalog: CheckCatalogView | undefined;
  /** La traza del turno: `errorStatus` es el status HTTP del error (null sin error). */
  trace: {
    pending: boolean;
    success: boolean;
    errorStatus: number | null | undefined;
    fidelity: "v1" | "v2" | null;
    steps: TraceStep[];
    decisions: EngineDecisionView[];
  };
  onClose: () => void;
  /** Prefijo de los ids (dos ventanas distintas en la misma página no chocan). */
  idPrefix: string;
}

type Tab = "resultado" | "pasos" | "jev";

const EYEBROW = "mb-1.5 text-[10px] font-semibold uppercase leading-none tracking-[0.08em] text-fg-faint";

export function TurnWindow({
  turn,
  botLabel,
  headerExtra,
  resetKey = "",
  replies,
  repliesPending,
  evaluation,
  catalog,
  trace,
  onClose,
  idPrefix,
}: TurnWindowProps) {
  const [tab, setTab] = useState<Tab>("resultado");
  // El paso elegido vale para lo que se muestra AHORA: con otro bot vuelve al primero.
  const [picked, setPicked] = useState<{ key: string; index: number }>({ key: resetKey, index: 0 });
  const selected = picked.key === resetKey ? picked.index : 0;
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  const layout = useMemo(() => layoutSequence(trace.steps), [trace.steps]);
  const row = layout.rows[Math.min(selected, Math.max(layout.rows.length - 1, 0))];

  const results = evaluation.results;
  const topics = results.flatMap((r) => r.topics);
  const failing = results.filter((r) => r.verdict === "falla").length;
  const decided = results.filter((r) => r.verdict === "falla" || r.verdict === "pasa").length;

  const n = turn.burst.length;
  const day = turn.at_ms !== null ? formatDayLabelEs(bogotaDayIsoFromMs(turn.at_ms)) : "";
  const time = turn.burst[n - 1]?.ts_ms ?? turn.at_ms;
  const meta = [day, hhmm(time), n > 1 ? `el cliente escribió ${n} mensajes seguidos` : "1 mensaje del cliente"].filter(Boolean).join(" · ");

  const decisions = trace.decisions;
  const tabs: Array<[Tab, string]> = [
    ["resultado", "Resultado"],
    ["pasos", "Paso a paso"],
    ...(decisions.length > 0 ? ([["jev", "Decisiones de Jev"]] as Array<[Tab, string]>) : []),
  ];
  const shownTab: Tab = tab === "jev" && decisions.length === 0 ? "resultado" : tab;
  const titleId = `${idPrefix}-title`;

  return (
    <Modal open labelledBy={titleId} onClose={onClose}>
      <div className="grid gap-2 border-b border-line bg-titlebar px-4 pt-3">
        <div className="flex flex-wrap items-start gap-3">
          <div>
            <p id={titleId} className="m-0 text-[15px] font-semibold">
              Hilo del turno {turn.turn}
            </p>
            <div className="text-xs tabular-nums text-fg-muted">{meta}</div>
            {results.length > 0 ? (
              <div role="group" aria-label="Resultado de este bot en el turno" className="mt-1.5 flex flex-wrap items-center gap-2 text-[12.5px]">
                <VerdictBadge verdict={turnVerdict(catalog, results)} />
                <span className="font-semibold">{checksHeadline(failing, decided)}</span>
              </div>
            ) : null}
          </div>
          {headerExtra ? <div className="ml-auto">{headerExtra}</div> : null}
          <button
            ref={closeRef}
            type="button"
            aria-label="Cerrar el hilo"
            onClick={onClose}
            className={
              (headerExtra ? "" : "ml-auto ") +
              "h-8 w-8 flex-none rounded-lg border border-line bg-white/[0.06] text-base text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            }
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
              id={`${idPrefix}-tab-${key}`}
              aria-selected={shownTab === key}
              aria-controls={`${idPrefix}-panel`}
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

      <div role="tabpanel" id={`${idPrefix}-panel`} aria-labelledby={`${idPrefix}-tab-${shownTab}`} className="flex min-h-0 flex-1 flex-col overflow-hidden">
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

            <section aria-label={`Lo que respondió ${botLabel}`}>
              <h3 className={EYEBROW}>{`Lo que respondió ${botLabel}`}</h3>
              {repliesPending ? <p className="m-0 text-[12.5px] text-fg-muted">Cargando la respuesta…</p> : null}
              <ReplyBubbles replies={replies} />
            </section>

            <section aria-label="Cómo le fue en este turno">
              <h3 className={EYEBROW}>Cómo le fue en este turno</h3>
              {evaluation.pending ? <p className="m-0 text-[12.5px] text-fg-muted">Cargando la evaluación…</p> : null}
              {evaluation.error ? <p className="m-0 text-[12.5px] text-fg-muted">No se pudo leer la evaluación de este bot.</p> : null}
              {!evaluation.pending && !evaluation.error && results.length === 0 ? (
                <p className="m-0 text-[12.5px] text-fg-muted">Este bot todavía no tiene evaluación en este turno.</p>
              ) : null}
              {results.length > 0 ? <CheckResults results={results} catalog={catalog} /> : null}
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
            {trace.fidelity === "v1" ? (
              <p className="m-0 border-b border-line bg-titlebar px-4 py-2 text-[12px] text-fg-muted">
                Este turno se registró con el formato anterior: no trae los tiempos de cada paso.
              </p>
            ) : null}
            <div className="grid min-h-0 flex-1 grid-cols-1 overflow-auto min-[760px]:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)] min-[760px]:overflow-hidden">
              <div className="bg-canvas p-3 min-[760px]:overflow-auto min-[760px]:border-r min-[760px]:border-line min-[760px]:px-2 min-[760px]:pb-4 min-[760px]:pt-2">
                {trace.pending ? <p className="p-3 text-[12.5px] text-fg-muted">Cargando el paso a paso…</p> : null}
                {trace.errorStatus !== null && trace.errorStatus !== undefined ? (
                  <p className="p-3 text-[12.5px] text-fg-muted">
                    {trace.errorStatus === 404 ? "Este bot no tiene el paso a paso de este turno." : "No se pudo leer el paso a paso de este turno."}
                  </p>
                ) : null}
                {trace.success && layout.rows.length === 0 ? <p className="p-3 text-[12.5px] text-fg-muted">Este turno no trae pasos.</p> : null}
                {trace.success && layout.rows.length > 0 ? (
                  <SequenceTrace layout={layout} selected={row?.index ?? 0} onSelect={(index) => setPicked({ key: resetKey, index })} idPrefix={`${idPrefix}-seq`} />
                ) : null}
              </div>
              <div aria-live="polite" className="bg-inspector px-[18px] py-4 min-[760px]:overflow-auto">
                {trace.success && row ? <TraceStepDetail step={trace.steps[row.stepIndex]} row={row} lanes={layout.lanes} steps={trace.steps} /> : null}
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
