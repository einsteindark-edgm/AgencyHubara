/**
 * Conversaciones de Calidad LLM con la vista del laboratorio, sobre
 * producción (decisión del operador, 2026-10-02). Se parece a Chats, sin
 * compositor:
 *  - izquierda: las conversaciones reales calificadas, con su resultado y el
 *    bot que las atendió (el actual o el bot Jev, el workflow nuevo);
 *  - centro: el hilo real con el botón «Ver hilo del turno» de cada turno,
 *    del color de su resultado;
 *  - derecha: cómo le fue en cada episodio y qué falló, por nombre.
 * Cada ráfaga, cada botón del turno y cada «turno N» de lo que falló abren la
 * ventana del turno: el resultado, el paso a paso y las decisiones de Jev.
 */

import { useMemo, useState } from "react";

import {
  engineDecisionsOf,
  useQualityConversations,
  useQualityEvaluations,
  useQualityThread,
  useQualityTurnTrace,
  type QualityBot,
  type QualityConversationRow,
  type QualityEvaluations,
  type QualityThread,
  type QualityThreadTurn,
} from "@plugins/agents_admin/frontend/entities/production-quality";
import { useCheckRegistry, type CheckRegistry } from "@plugins/agents_admin/frontend/entities/scorecard";
import {
  customerLabel,
  productionReplies,
  productionThreadView,
  turnVerdict,
  worstVerdict,
  type QualityVerdict,
  type TraceStep,
} from "@/shared/lib";
import { CheckResults, ConversationThread, TurnWindow, VerdictBadge } from "@/shared/ui";

interface Props {
  days: number;
  bot: QualityBot | null;
  /** Solo las conversaciones con este resultado (la alerta de FALLA). */
  verdictFilter?: QualityVerdict | null;
  /** La conversación que se abre primero (la que eligió el Resumen). */
  initialSid?: string | null;
  /** Desde una falla de la matriz: abre la ventana del turno de ese check en ese episodio. */
  initialFocus?: { episodeId: string; checkId: string } | null;
}

const EYEBROW = "text-[10px] font-semibold uppercase leading-none tracking-[0.08em] text-fg-faint";

/** Qué quiere decir cada resultado (misma regla que el scorecard). */
const VERDICT_HELP: Array<[QualityVerdict, string]> = [
  ["PASA", "Cumplió todo lo importante."],
  ["ALERTA", "Falló algo importante (un check mayor)."],
  ["FALLA", "Falló algo crítico."],
  ["SIN_DATOS", "No se pudo evaluar."],
];

/** El bot de un episodio en palabras. */
const BOT_NAME: Record<string, string> = { actual: "Bot actual", nuevo: "Bot Jev", mixto: "Los dos bots" };

function rowVerdict(row: QualityConversationRow): QualityVerdict {
  return worstVerdict(Object.values(row.verdicts));
}

/** Quién atendió la conversación: un bot, o «los dos bots» si cambió entre episodios. */
function rowBot(row: QualityConversationRow): string | null {
  const bots = [...new Set(Object.values(row.bots))];
  if (bots.length === 0) return null;
  return bots.length === 1 ? (BOT_NAME[bots[0]] ?? null) : BOT_NAME.mixto;
}

/** El turno de un check en un episodio: el primero en que falló o, si no falló, el primero en que se juzgó. */
function focusTurn(
  thread: QualityThread | undefined,
  evals: QualityEvaluations | undefined,
  focus: { episodeId: string; checkId: string } | null,
): QualityThreadTurn | null {
  if (!focus || !thread || !evals) return null;
  const results = (evals.episodes.find((e) => e.episode_id === focus.episodeId)?.results ?? [])
    .filter((r) => r.check_id === focus.checkId && r.turn !== null)
    .sort((a, b) => (a.turn ?? 0) - (b.turn ?? 0));
  const hit = results.find((r) => r.verdict === "falla") ?? results[0];
  return hit ? (thread.turns.find((t) => t.episode_id === focus.episodeId && t.turn === hit.turn) ?? null) : null;
}

function turnResults(evals: QualityEvaluations | undefined, turn: QualityThreadTurn) {
  const episode = evals?.episodes.find((e) => e.episode_id === turn.episode_id);
  return episode ? episode.results.filter((r) => r.turn === turn.turn) : [];
}

export function QualityConversations({ days, bot, verdictFilter = null, initialSid = null, initialFocus = null }: Props) {
  const conversations = useQualityConversations(days, bot);
  const rows = useMemo(
    () => (conversations.data?.conversations ?? []).filter((r) => verdictFilter === null || rowVerdict(r) === verdictFilter),
    [conversations.data, verdictFilter],
  );
  const [picked, setPicked] = useState<string | null>(initialSid);
  const [side, setSide] = useState<"resultado" | "fallas">("resultado");
  const [chosen, setChosen] = useState<QualityThreadTurn | null>(null);
  // La falla de la matriz espera al hilo y a la evaluación; elegir o cerrar otro turno la descarta.
  const [focus, setFocus] = useState(initialFocus);
  const registry = useCheckRegistry();

  const row = rows.find((r) => r.session_id === picked) ?? rows[0] ?? null;
  const sid = row?.session_id ?? null;
  const thread = useQualityThread(sid);
  const evals = useQualityEvaluations(sid);
  const open = chosen ?? (sid === initialSid ? focusTurn(thread.data, evals.data, focus) : null);
  const setOpen = (turn: QualityThreadTurn | null) => {
    setFocus(null);
    setChosen(turn);
  };

  if (conversations.isPending) return <p className="p-4 text-[12.5px] text-fg-muted">Cargando las conversaciones…</p>;
  if (conversations.isError) return <p className="p-4 text-[12.5px] text-fg-muted">No se pudieron leer las conversaciones.</p>;
  if (row === null) {
    return (
      <p className="rounded-lg border border-dashed border-line-strong p-4 text-sm text-fg-muted">
        {verdictFilter ? "Ninguna conversación con ese resultado en esta ventana." : "Todavía no hay conversaciones calificadas en esta ventana."}
      </p>
    );
  }

  return (
    <div className="grid min-h-[440px] grid-cols-1 overflow-hidden rounded-lg border border-line min-[900px]:h-[calc(100vh-170px)] min-[900px]:grid-cols-[220px_minmax(0,1fr)_268px]">
      <ul
        aria-label="Conversaciones calificadas"
        className="m-0 flex list-none overflow-x-auto border-b border-line bg-inspector p-0 min-[900px]:block min-[900px]:overflow-y-auto min-[900px]:border-b-0 min-[900px]:border-r"
      >
        {rows.map((r) => {
          const who = rowBot(r);
          return (
            <li key={r.session_id} className="min-w-[200px] border-r border-line min-[900px]:min-w-0 min-[900px]:border-b min-[900px]:border-r-0">
              <button
                type="button"
                aria-current={r.session_id === row.session_id}
                onClick={() => setPicked(r.session_id)}
                className={
                  "block w-full px-3 py-2.5 text-left text-[12.5px] font-medium leading-snug text-fg focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent " +
                  (r.session_id === row.session_id ? "bg-accent-soft" : "bg-transparent")
                }
              >
                {customerLabel(r.session_id)}
                <small className="mt-0.5 block text-[11px] font-normal text-fg-muted">
                  {`${r.turns} ${r.turns === 1 ? "turno" : "turnos"}${r.episodes.length > 1 ? ` · ${r.episodes.length} episodios` : ""}`}
                </small>
                <span className="mt-1.5 flex items-center justify-between gap-2">
                  <span className="truncate text-[11px] font-normal text-fg-muted">{who}</span>
                  <VerdictBadge verdict={rowVerdict(r)} />
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      <div className="flex min-h-0 min-w-0 flex-col">
        {thread.isPending ? <p className="flex-1 bg-canvas p-4 text-[12.5px] text-fg-muted">Cargando el hilo…</p> : null}
        {thread.isError ? <p className="flex-1 bg-canvas p-4 text-[12.5px] text-fg-muted">No se pudo leer el hilo de esta conversación.</p> : null}
        {thread.data ? (
          <Thread thread={thread.data} evals={evals.data} evaluating={evals.isPending} registry={registry.data} onOpenTurn={setOpen} />
        ) : null}
      </div>

      <Inspector
        row={row}
        side={side}
        onSide={setSide}
        evals={evals}
        registry={registry.data}
        onOpenTurn={(episodeId, turn) => {
          const found = thread.data?.turns.find((t) => t.episode_id === episodeId && t.turn === turn);
          if (found) setOpen(found);
        }}
      />

      {open && thread.data ? (
        <TurnWindowFor
          key={open.turn_key}
          sid={row.session_id}
          turn={open}
          thread={thread.data}
          evals={evals}
          registry={registry.data}
          bot={row.bots[open.episode_id]}
          onClose={() => setOpen(null)}
        />
      ) : null}
    </div>
  );
}

function Thread({
  thread,
  evals,
  evaluating,
  registry,
  onOpenTurn,
}: {
  thread: QualityThread;
  evals: QualityEvaluations | undefined;
  evaluating: boolean;
  registry: CheckRegistry | undefined;
  onOpenTurn: (turn: QualityThreadTurn) => void;
}) {
  // El resultado de cada turno sale de la MISMA evaluación y la MISMA regla
  // que el encabezado de la ventana del turno.
  const items = useMemo(
    () =>
      productionThreadView(thread, (turn) => {
        const results = turnResults(evals, turn);
        return results.length > 0 ? turnVerdict(registry, results) : null;
      }),
    [thread, evals, registry],
  );
  return <ConversationThread items={items} evaluating={evaluating} onOpenTurn={onOpenTurn} />;
}

// ── Panel derecho: el resultado y qué falló ─────────────────────────────────

function Inspector({
  row,
  side,
  onSide,
  evals,
  registry,
  onOpenTurn,
}: {
  row: QualityConversationRow;
  side: "resultado" | "fallas";
  onSide: (s: "resultado" | "fallas") => void;
  evals: ReturnType<typeof useQualityEvaluations>;
  registry: CheckRegistry | undefined;
  onOpenTurn: (episodeId: string, turn: number) => void;
}) {
  const tabs: Array<["resultado" | "fallas", string]> = [
    ["resultado", "Resultado"],
    ["fallas", "Qué falló"],
  ];
  return (
    <div className="border-t border-line bg-inspector px-3 pb-3.5 pt-2.5 text-[12.5px] text-fg-soft min-[900px]:overflow-y-auto min-[900px]:border-l min-[900px]:border-t-0">
      <div role="tablist" aria-label="Panel de evaluación" className="mb-2 flex gap-0.5 border-b border-line">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            id={`quality-side-${key}`}
            aria-selected={side === key}
            aria-controls={`quality-side-panel-${key}`}
            onClick={() => onSide(key)}
            className={
              "border-0 border-b-2 bg-transparent px-2 py-[9px] text-xs font-medium leading-none focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent " +
              (side === key ? "border-accent text-fg" : "border-transparent text-fg-muted")
            }
          >
            {label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`quality-side-panel-${side}`} aria-labelledby={`quality-side-${side}`}>
        {side === "resultado" ? <Result row={row} /> : <Failures evals={evals} registry={registry} onOpenTurn={onOpenTurn} />}
      </div>
    </div>
  );
}

function Result({ row }: { row: QualityConversationRow }) {
  return (
    <>
      <h5 className={"mb-1.5 mt-3 " + EYEBROW}>Cómo le fue en esta conversación</h5>
      {row.episodes.length > 1 ? (
        <p className="m-0 mb-1 text-[11px] text-fg-faint">Cada episodio es un intento de venta distinto dentro de la misma conversación.</p>
      ) : null}
      <ul className="m-0 grid list-none gap-0.5 p-0">
        {row.episodes.map((ep, k) => (
          <li key={ep} className="flex items-center justify-between gap-2 rounded-md px-1.5 py-[5px]">
            <span>
              {row.episodes.length > 1 ? `Episodio ${k + 1}` : "La conversación"}
              {row.bots[ep] ? <small className="ml-1.5 text-[11px] text-fg-muted">{BOT_NAME[row.bots[ep]]}</small> : null}
            </span>
            {row.verdicts[ep] ? <VerdictBadge verdict={row.verdicts[ep]} /> : <span className="text-[11.5px] text-fg-faint">sin evaluar</span>}
          </li>
        ))}
      </ul>
      <h5 className={"mb-1.5 mt-4 " + EYEBROW}>Qué quiere decir cada resultado</h5>
      <ul className="m-0 grid list-none gap-1.5 p-0 text-[11.5px]">
        {VERDICT_HELP.map(([verdict, text]) => (
          <li key={verdict} className="flex items-center gap-2 text-fg-muted">
            <VerdictBadge verdict={verdict} />
            <span>{text}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

function Failures({
  evals,
  registry,
  onOpenTurn,
}: {
  evals: ReturnType<typeof useQualityEvaluations>;
  registry: CheckRegistry | undefined;
  onOpenTurn: (episodeId: string, turn: number) => void;
}) {
  if (evals.isPending) return <p className="text-fg-muted">Cargando las evaluaciones…</p>;
  if (evals.isError) return <p className="text-fg-muted">No se pudieron leer las evaluaciones.</p>;
  const episodes = evals.data.episodes;
  if (episodes.length === 0) return <p className="text-fg-muted">Esta conversación todavía no tiene evaluaciones.</p>;
  return (
    <>
      <h5 className={"mb-2 mt-3 " + EYEBROW}>Qué falló</h5>
      {episodes.map((ep, k) => (
        <section key={ep.episode_id} aria-label={`Episodio ${k + 1}`} className="mb-3">
          {episodes.length > 1 ? (
            <h6 className={"mb-1.5 flex items-center gap-2 " + EYEBROW}>
              {`Episodio ${k + 1}`} <VerdictBadge verdict={ep.verdict} />
            </h6>
          ) : null}
          <CheckResults results={ep.results} catalog={registry} scope="conversation" onOpenTurn={(turn) => onOpenTurn(ep.episode_id, turn)} />
        </section>
      ))}
    </>
  );
}

// ── La ventana del turno ────────────────────────────────────────────────────

function TurnWindowFor({
  sid,
  turn,
  thread,
  evals,
  registry,
  bot,
  onClose,
}: {
  sid: string;
  turn: QualityThreadTurn;
  thread: QualityThread;
  evals: ReturnType<typeof useQualityEvaluations>;
  registry: CheckRegistry | undefined;
  bot: string | undefined;
  onClose: () => void;
}) {
  const trace = useQualityTurnTrace(sid, turn.turn_key);
  const steps = useMemo(() => (trace.data?.steps ?? []) as TraceStep[], [trace.data]);
  const decisions = useMemo(() => (trace.data ? engineDecisionsOf(trace.data.trace) : []), [trace.data]);
  const replies = useMemo(() => productionReplies(thread, turn), [thread, turn]);
  const errorStatus = trace.isError ? ((trace.error as { status?: number } | null)?.status ?? 0) : null;
  return (
    <TurnWindow
      idPrefix="quality-turn"
      turn={turn}
      botLabel={bot === "nuevo" ? "el bot Jev" : "el bot"}
      replies={replies}
      repliesPending={false}
      evaluation={{ pending: evals.isPending, error: evals.isError, results: turnResults(evals.data, turn) }}
      catalog={registry}
      trace={{ pending: trace.isPending, success: trace.isSuccess, errorStatus, fidelity: trace.data?.fidelity ?? null, steps, decisions }}
      onClose={onClose}
    />
  );
}
