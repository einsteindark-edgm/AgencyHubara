/**
 * Pestaña "Conversaciones" del laboratorio (plan §11, diseño §09). Se parece
 * a Chats, sin compositor: la lista de conversaciones del banco con el
 * veredicto de cada bot; el hilo con un selector de bot; a la derecha el
 * resumen de evaluaciones y la subpestaña Evaluaciones. Cada ráfaga y cada
 * botón "Ver hilo del turno" abren el modal del hilo de ese turno.
 */

import { useMemo, useState } from "react";

import { formatDayLabelEs } from "@/shared/lib";
import {
  armLabel,
  customerLabel,
  useRunConversations,
  useRunEvaluations,
  useRunThread,
  VerdictBadge,
  worstVerdict,
  type ConversationRow,
  type EvalResult,
  type LabRun,
  type ThreadTurn,
} from "@plugins/lab/frontend/entities/lab-run";

import { buildThreadView, PRODUCTION_ARM, type ThreadItem } from "../lib/thread-view";
import { TurnTraceModal } from "./TurnTraceModal";

interface Props {
  run: LabRun;
}

const VERDICT_RANK: Record<string, number> = { falla: 0, pasa: 1 };

function armsOf(run: LabRun): string[] {
  const arms = run.arms.length ? run.arms : [PRODUCTION_ARM];
  return arms.includes(PRODUCTION_ARM) ? arms : [PRODUCTION_ARM, ...arms];
}

function rowVerdict(row: ConversationRow, arm: string) {
  const byEpisode = row.verdicts[arm];
  return byEpisode ? worstVerdict(Object.values(byEpisode)) : null;
}

export function LabConversations({ run }: Props) {
  const conversations = useRunConversations(run.run_id);
  const rows = conversations.data?.conversations ?? [];
  const [picked, setPicked] = useState<string | null>(null);
  const sid = picked ?? rows[0]?.session_id ?? null;
  const [arm, setArm] = useState(PRODUCTION_ARM);
  const [side, setSide] = useState<"res" | "ev">("res");
  const [openTurn, setOpenTurn] = useState<ThreadTurn | null>(null);
  const arms = armsOf(run);

  if (conversations.isPending) return <p className="bg-canvas p-4 text-[12.5px] text-fg-muted">Cargando las conversaciones del banco…</p>;
  if (conversations.isError || rows.length === 0) {
    return <p className="bg-canvas p-4 text-[12.5px] text-fg-muted">Esta corrida todavía no publicó conversaciones.</p>;
  }
  const row = rows.find((r) => r.session_id === sid) ?? rows[0];

  return (
    <div className="grid min-h-[440px] grid-cols-1 min-[900px]:grid-cols-[200px_minmax(0,1fr)_248px]">
      <ul
        aria-label="Conversaciones del banco"
        className="m-0 flex list-none overflow-x-auto border-b border-line bg-inspector p-0 min-[900px]:block min-[900px]:overflow-y-auto min-[900px]:border-b-0 min-[900px]:border-r"
      >
        {rows.map((r) => (
          <li key={r.session_id} className="min-w-[190px] border-r border-line min-[900px]:min-w-0 min-[900px]:border-b min-[900px]:border-r-0">
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
                {`${r.turns} ${r.turns === 1 ? "turno" : "turnos"} · ${r.episodes.length} ${r.episodes.length === 1 ? "episodio" : "episodios"}`}
              </small>
              <span className="mt-1.5 flex flex-wrap gap-1">
                {arms.map((a) => {
                  const v = rowVerdict(r, a);
                  return v ? <VerdictBadge key={a} verdict={v} prefix={armLabel(a)} /> : null;
                })}
              </span>
            </button>
          </li>
        ))}
      </ul>

      <div className="flex min-w-0 flex-col">
        <div className="flex flex-wrap items-center gap-2 border-b border-line bg-canvas px-3 py-2">
          <span className="text-[10px] font-semibold uppercase leading-none tracking-[0.08em] text-fg-faint">Bot</span>
          <div role="group" aria-label="Bot" className="inline-flex flex-wrap rounded-lg border border-line bg-white/[0.06] p-0.5">
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
        </div>
        <Thread run={run.run_id} sid={row.session_id} arm={arm} onOpenTurn={setOpenTurn} />
      </div>

      <Inspector run={run.run_id} row={row} arms={arms} arm={arm} side={side} onSide={setSide} />

      {openTurn ? (
        <TurnTraceModal
          run={run.run_id}
          sid={row.session_id}
          turn={openTurn}
          arms={arms.filter((a) => a === PRODUCTION_ARM || openTurn.outputs[a])}
          initialArm={arm === PRODUCTION_ARM || openTurn.outputs[arm] ? arm : PRODUCTION_ARM}
          onClose={() => setOpenTurn(null)}
        />
      ) : null}
    </div>
  );
}

function Thread({ run, sid, arm, onOpenTurn }: { run: string; sid: string; arm: string; onOpenTurn: (turn: ThreadTurn) => void }) {
  const thread = useRunThread(run, sid);
  const items = useMemo(() => (thread.data ? buildThreadView(thread.data, arm) : []), [thread.data, arm]);

  if (thread.isPending) return <p className="flex-1 bg-canvas p-4 text-[12.5px] text-fg-muted">Cargando el hilo…</p>;
  if (thread.isError) return <p className="flex-1 bg-canvas p-4 text-[12.5px] text-fg-muted">No se pudo leer el hilo de esta conversación.</p>;

  return (
    <div className="flex flex-1 flex-col gap-1.5 overflow-y-auto bg-canvas px-4 py-3.5">
      {items.map((it) => (
        <ThreadRow key={it.key} item={it} onOpenTurn={onOpenTurn} />
      ))}
    </div>
  );
}

function Bubble({ dir, text, time, hasImage }: { dir: "in" | "out" | "comp"; text: string; time: string; hasImage?: boolean }) {
  const cls =
    dir === "in"
      ? "self-start rounded-bl-[5px] bg-bubble-out text-fg"
      : dir === "comp"
        ? "self-end border border-info/40 bg-info-soft text-white"
        : "self-end rounded-br-[5px] bg-bubble-in text-white";
  return (
    <div className={"max-w-[82%] whitespace-pre-line break-words rounded-[14px] px-3 py-2 text-[13.5px] leading-[1.42] " + cls}>
      {text || (hasImage ? "📷 Foto" : "")}
      {time ? <div className={"mt-[3px] text-right text-[10px] tabular-nums " + (dir === "in" ? "text-fg-faint" : "text-white/75")}>{time}</div> : null}
    </div>
  );
}

function ThreadRow({ item, onOpenTurn }: { item: ThreadItem; onOpenTurn: (turn: ThreadTurn) => void }) {
  switch (item.type) {
    case "day":
      return <div className="self-center px-2.5 py-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-fg-faint">{formatDayLabelEs(item.day)}</div>;
    case "msg":
      if (item.dir === "system") return <div className="self-center text-[11.5px] text-fg-muted">{item.text}</div>;
      return <Bubble dir={item.dir} text={item.text} time={item.time} hasImage={item.hasImage} />;
    case "note":
      return <div className="self-end text-[11.5px] italic text-fg-muted">{item.text}</div>;
    case "burst":
      return (
        <button
          type="button"
          aria-label={`Ver el hilo de la ráfaga de ${item.messages.length} mensajes`}
          onClick={() => onOpenTurn(item.turn)}
          className="flex w-full gap-2 self-start border-0 bg-transparent p-0 text-left text-inherit focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <span aria-hidden="true" className="w-[3px] flex-none rounded-[3px] bg-violet opacity-80" />
          <span className="flex min-w-0 flex-1 flex-col gap-1.5">
            <span className="flex items-center gap-1.5 text-[10px] font-semibold uppercase leading-none tracking-[0.06em] text-violet">
              {`Ráfaga · ${item.messages.length} mensajes${item.spanS > 0 ? ` · ${item.spanS} s` : ""}`}
            </span>
            {item.messages.map((m, k) => (
              <span key={k} className="block max-w-[90%] whitespace-pre-line break-words rounded-[14px] rounded-bl-[5px] bg-bubble-out px-3 py-2 text-[13.5px] leading-[1.42] text-fg">
                {m.text}
                {m.time ? <span className="mt-[3px] block text-right text-[10px] tabular-nums text-fg-faint">{m.time}</span> : null}
              </span>
            ))}
          </span>
        </button>
      );
    case "chip":
      return (
        <button
          type="button"
          onClick={() => onOpenTurn(item.turn)}
          className="inline-flex items-center gap-2 self-end rounded-full border border-line-strong bg-white/[0.06] px-3 py-[7px] text-xs font-medium leading-none text-fg hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <i aria-hidden="true" className={"inline-block h-2 w-2 rounded-full " + (item.tone === "warn" ? "bg-warn" : "bg-neutral")} />
          Ver hilo del turno <small className="text-[11.5px] text-fg-muted">{item.sub}</small>
        </button>
      );
  }
}

function Inspector({
  run,
  row,
  arms,
  arm,
  side,
  onSide,
}: {
  run: string;
  row: ConversationRow;
  arms: string[];
  arm: string;
  side: "res" | "ev";
  onSide: (s: "res" | "ev") => void;
}) {
  const tabs: Array<["res" | "ev", string]> = [
    ["res", "Resumen"],
    ["ev", "Evaluaciones"],
  ];
  return (
    <div className="border-t border-line bg-inspector px-3 pb-3.5 pt-2.5 text-[12.5px] text-fg-soft min-[900px]:border-l min-[900px]:border-t-0">
      <div role="tablist" aria-label="Panel de evaluación" className="mb-2 flex gap-0.5 border-b border-line">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            id={`lab-side-${key}`}
            aria-selected={side === key}
            aria-controls={`lab-side-panel-${key}`}
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
      <div role="tabpanel" id={`lab-side-panel-${side}`} aria-labelledby={`lab-side-${side}`}>
        {side === "res" ? <Summary row={row} arms={arms} arm={arm} /> : <EvaluationList run={run} sid={row.session_id} arm={arm} />}
      </div>
    </div>
  );
}

function Summary({ row, arms, arm }: { row: ConversationRow; arms: string[]; arm: string }) {
  const episodes = row.verdicts[arm] ?? {};
  return (
    <>
      <h5 className="mb-1.5 mt-3 text-[10px] font-semibold uppercase leading-none tracking-[0.08em] text-fg-faint">Veredicto por bot</h5>
      {arms.map((a) => {
        const v = rowVerdict(row, a);
        return (
          <div key={a} className={"flex items-center justify-between gap-2 rounded-md px-1.5 py-[5px] " + (a === arm ? "bg-white/5 text-fg" : "")}>
            <span>{armLabel(a)}</span>
            {v ? <VerdictBadge verdict={v} /> : <span className="text-[11.5px] text-fg-faint">pendiente</span>}
          </div>
        );
      })}
      <h5 className="mb-1.5 mt-3 text-[10px] font-semibold uppercase leading-none tracking-[0.08em] text-fg-faint">{`Episodios · ${armLabel(arm)}`}</h5>
      {row.episodes.map((ep) => (
        <div key={ep} className="flex items-center justify-between gap-2 rounded-md px-1.5 py-[5px]">
          <span className="font-mono text-[11.5px]">{ep}</span>
          {episodes[ep] ? <VerdictBadge verdict={episodes[ep]} /> : <span className="text-[11.5px] text-fg-faint">sin evaluar</span>}
        </div>
      ))}
    </>
  );
}

function EvaluationList({ run, sid, arm }: { run: string; sid: string; arm: string }) {
  const evals = useRunEvaluations(run, sid, arm);
  if (evals.isPending) return <p className="text-fg-muted">Cargando las evaluaciones…</p>;
  if (evals.isError) return <p className="text-fg-muted">No se pudieron leer las evaluaciones.</p>;
  const episodes = evals.data.episodes;
  if (episodes.length === 0) return <p className="text-fg-muted">{`${armLabel(arm)} todavía no tiene evaluaciones en esta conversación.`}</p>;
  return (
    <>
      {episodes.map((ep) => {
        const shown = ep.results
          .filter((r): r is EvalResult => r.verdict === "falla" || r.verdict === "pasa")
          .sort((a, b) => (VERDICT_RANK[a.verdict] ?? 9) - (VERDICT_RANK[b.verdict] ?? 9));
        // Modo turno (bots simulados): los checks que dependen de turnos
        // posteriores (cierre, pedido registrado) no se deciden con un turno.
        const noSignal = ep.results.filter((r) => r.verdict === "sin_senal").length;
        return (
          <section key={ep.episode_id} aria-label={`Episodio ${ep.episode_id}`}>
            <h5 className="mb-1 mt-3 flex items-center gap-2 text-[10px] font-semibold uppercase leading-none tracking-[0.08em] text-fg-faint">
              {ep.episode_id} <VerdictBadge verdict={ep.verdict} />
            </h5>
            {shown.map((r) => (
              // En modo turno el mismo check aparece una vez por turno.
              <article key={`${r.check_id}:${r.turn ?? "ep"}`} className="border-b border-line py-2">
                <div className="flex items-center gap-1.5">
                  <b className="text-fg">{r.check_id}</b>
                  <span className={"rounded-full px-1.5 py-[3px] text-[9.5px] font-semibold leading-none " + (r.verdict === "falla" ? "bg-danger-soft text-danger" : "bg-ok-soft text-ok")}>
                    {r.verdict}
                  </span>
                  <small className="ml-auto text-[11px] text-fg-faint">{r.turn !== null ? `turno ${r.turn}` : "—"}</small>
                </div>
                {r.evidence || r.critique ? <p className="mb-0 mt-1 text-xs text-fg-muted">{r.critique ?? r.evidence}</p> : null}
              </article>
            ))}
            {noSignal > 0 ? (
              <p className="mb-0 mt-1.5 text-[11px] text-fg-faint">
                {`${noSignal} ${noSignal === 1 ? "check sin señal" : "checks sin señal"}: dependen de turnos posteriores (cierre, pedido registrado).`}
              </p>
            ) : null}
          </section>
        );
      })}
      <p className="mt-2.5 text-[11.5px] text-fg-muted">Cada check trae su evidencia y la crítica del juez, igual que en Calidad LLM.</p>
    </>
  );
}
