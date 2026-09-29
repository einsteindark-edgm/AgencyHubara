/**
 * Pestaña "Conversaciones" del laboratorio (plan §11, diseño §09; revisión
 * 2026-09-29: el operador no entendía los códigos). Se parece a Chats, sin
 * compositor:
 *  - izquierda: las conversaciones del banco con el resultado de cada bot;
 *  - centro: el hilo con un selector de bot, o el modo Comparar (cada turno
 *    con lo que respondió cada bot, lado a lado, y cómo le fue);
 *  - derecha: cómo le fue a cada bot en la conversación y qué falló, por
 *    nombre.
 * Cada ráfaga, cada «Ver hilo del turno» y cada «Ver cómo lo decidió» abren el
 * modal del hilo de ese turno.
 */

import { useMemo, useState } from "react";

import { bogotaDayIsoFromMs, formatDayLabelEs } from "@/shared/lib";
import {
  ARM_HELP,
  armLabel,
  checkView,
  customerLabel,
  turnVerdict,
  useCheckCatalog,
  useRunConversations,
  useRunEvaluationsByArm,
  useRunThread,
  VerdictBadge,
  worstVerdict,
  type CheckCatalog,
  type ConversationRow,
  type EpisodeVerdict,
  type EvalResult,
  type Evaluations,
  type LabRun,
  type LabThread,
  type ThreadTurn,
} from "@plugins/lab/frontend/entities/lab-run";

import { buildThreadView, PRODUCTION_ARM, turnReplies, type ThreadItem } from "../lib/thread-view";
import { CheckResults, LevelPill } from "./CheckResults";
import { TurnTraceModal } from "./TurnTraceModal";

interface Props {
  run: LabRun;
}

type View = "bot" | "compare";

const EYEBROW = "text-[10px] font-semibold uppercase leading-none tracking-[0.08em] text-fg-faint";
const COMPARE_HELP = "Cada turno del cliente con lo que respondió cada bot y cómo le fue.";

/** Qué quiere decir cada resultado (misma regla que el scorecard). */
const VERDICT_HELP: Array<[EpisodeVerdict, string]> = [
  ["PASA", "Cumplió todo lo importante."],
  ["ALERTA", "Falló algo importante (un check mayor)."],
  ["FALLA", "Falló algo crítico."],
  ["SIN_DATOS", "No se pudo evaluar."],
];

function armsOf(run: LabRun): string[] {
  const arms = run.arms.length ? run.arms : [PRODUCTION_ARM];
  return arms.includes(PRODUCTION_ARM) ? arms : [PRODUCTION_ARM, ...arms];
}

function rowVerdict(row: ConversationRow, arm: string) {
  const byEpisode = row.verdicts[arm];
  return byEpisode ? worstVerdict(Object.values(byEpisode)) : null;
}

function resultsOf(data: Evaluations | undefined, episodeId?: string): EvalResult[] {
  const episodes = data?.episodes ?? [];
  return episodes.filter((e) => episodeId === undefined || e.episode_id === episodeId).flatMap((e) => e.results);
}

export function LabConversations({ run }: Props) {
  const conversations = useRunConversations(run.run_id);
  const rows = conversations.data?.conversations ?? [];
  const [picked, setPicked] = useState<string | null>(null);
  const sid = picked ?? rows[0]?.session_id ?? null;
  const [arm, setArm] = useState(PRODUCTION_ARM);
  const [view, setView] = useState<View>("bot");
  const [side, setSide] = useState<"bots" | "fallas">("bots");
  const [open, setOpen] = useState<{ turn: ThreadTurn; arm: string } | null>(null);
  const arms = armsOf(run);

  if (conversations.isPending) return <p className="bg-canvas p-4 text-[12.5px] text-fg-muted">Cargando las conversaciones del banco…</p>;
  if (conversations.isError || rows.length === 0) {
    return <p className="bg-canvas p-4 text-[12.5px] text-fg-muted">Esta corrida todavía no publicó conversaciones.</p>;
  }
  const row = rows.find((r) => r.session_id === sid) ?? rows[0];

  return (
    <div className="grid min-h-[440px] grid-cols-1 min-[900px]:h-full min-[900px]:grid-cols-[210px_minmax(0,1fr)_268px] min-[900px]:overflow-hidden">
      <ul
        aria-label="Conversaciones del banco"
        className="m-0 flex list-none overflow-x-auto border-b border-line bg-inspector p-0 min-[900px]:block min-[900px]:overflow-y-auto min-[900px]:border-b-0 min-[900px]:border-r"
      >
        {rows.map((r) => (
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
              <span className="mt-1.5 grid grid-cols-[1fr_auto] items-center gap-x-2 gap-y-1">
                {arms.map((a) => {
                  const v = rowVerdict(r, a);
                  return (
                    <span key={a} className="contents">
                      <span className="truncate text-[11px] font-normal text-fg-muted">{armLabel(a)}</span>
                      {v ? <VerdictBadge verdict={v} /> : <span className="text-[10.5px] font-normal text-fg-faint">sin resultado</span>}
                    </span>
                  );
                })}
              </span>
            </button>
          </li>
        ))}
      </ul>

      <div className="flex min-h-0 min-w-0 flex-col">
        <div className="grid gap-1.5 border-b border-line bg-canvas px-3 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className={EYEBROW}>Ver</span>
            <div role="group" aria-label="Bot" className="inline-flex flex-wrap rounded-lg border border-line bg-white/[0.06] p-0.5">
              {arms.map((a) => (
                <button
                  key={a}
                  type="button"
                  aria-pressed={view === "bot" && a === arm}
                  onClick={() => {
                    setArm(a);
                    setView("bot");
                  }}
                  className={
                    "rounded-md border-0 px-2.5 py-[7px] text-xs font-medium leading-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent " +
                    (view === "bot" && a === arm ? "bg-accent-soft text-white" : "bg-transparent text-fg-muted")
                  }
                >
                  {armLabel(a)}
                </button>
              ))}
              <button
                type="button"
                aria-pressed={view === "compare"}
                onClick={() => setView("compare")}
                className={
                  "rounded-md border-0 px-2.5 py-[7px] text-xs font-medium leading-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent " +
                  (view === "compare" ? "bg-accent-soft text-white" : "bg-transparent text-fg-muted")
                }
              >
                Comparar
              </button>
            </div>
          </div>
          <p className="m-0 text-[11.5px] text-fg-muted">{view === "compare" ? COMPARE_HELP : (ARM_HELP[arm] ?? "")}</p>
        </div>
        {view === "compare" ? (
          <Compare run={run.run_id} sid={row.session_id} arms={arms} onOpen={(turn, a) => setOpen({ turn, arm: a })} />
        ) : (
          <Thread run={run.run_id} sid={row.session_id} arm={arm} onOpenTurn={(turn) => setOpen({ turn, arm })} />
        )}
      </div>

      <Inspector run={run.run_id} row={row} arms={arms} arm={arm} side={side} onSide={setSide} onOpenTurn={(turn) => setOpen({ turn, arm })} />

      {open ? (
        <TurnTraceModal
          run={run.run_id}
          sid={row.session_id}
          turn={open.turn}
          arms={arms.filter((a) => a === PRODUCTION_ARM || open.turn.outputs[a])}
          initialArm={open.arm === PRODUCTION_ARM || open.turn.outputs[open.arm] ? open.arm : PRODUCTION_ARM}
          onClose={() => setOpen(null)}
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

/** Etiqueta de un mensaje que escribió una persona del equipo (no el bot). */
const TEAM_LABEL = "Persona del equipo";

function Bubble({ dir, text, time, hasImage, byHuman }: { dir: "in" | "out" | "comp"; text: string; time: string; hasImage?: boolean; byHuman?: boolean }) {
  const cls =
    dir === "in"
      ? "self-start rounded-bl-[5px] bg-bubble-out text-fg"
      : dir === "comp"
        ? "self-end border border-info/40 bg-info-soft text-white"
        : "self-end rounded-br-[5px] bg-bubble-in text-white";
  return (
    <div className={"max-w-[82%] whitespace-pre-line break-words rounded-[14px] px-3 py-2 text-[13.5px] leading-[1.42] " + cls + (byHuman ? " ring-2 ring-warn/60" : "")}>
      {byHuman ? <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.06em] text-white/80">{TEAM_LABEL}</div> : null}
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
      return <Bubble dir={item.dir} text={item.text} time={item.time} hasImage={item.hasImage} byHuman={item.byHuman} />;
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
              {`${item.messages.length} mensajes seguidos${item.spanS > 0 ? ` en ${item.spanS} s` : ""}`}
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

// ── Comparar: cada turno con lo que respondió cada bot ──────────────────────

function Compare({ run, sid, arms, onOpen }: { run: string; sid: string; arms: string[]; onOpen: (turn: ThreadTurn, arm: string) => void }) {
  const thread = useRunThread(run, sid);
  const evals = useRunEvaluationsByArm(run, sid, arms);
  const catalog = useCheckCatalog();

  if (thread.isPending) return <p className="flex-1 bg-canvas p-4 text-[12.5px] text-fg-muted">Cargando el hilo…</p>;
  if (thread.isError) return <p className="flex-1 bg-canvas p-4 text-[12.5px] text-fg-muted">No se pudo leer el hilo de esta conversación.</p>;
  const turns = [...thread.data.turns].sort((a, b) => (a.at_ms ?? 0) - (b.at_ms ?? 0));

  return (
    <div className="flex flex-1 flex-col gap-4 overflow-y-auto bg-canvas px-4 py-3.5">
      {turns.map((turn) => (
        <section key={turn.turn_key} aria-label={`Turno ${turn.turn}`} className="grid gap-2">
          <div className={EYEBROW}>
            {`Turno ${turn.turn}${turn.at_ms !== null ? ` · ${formatDayLabelEs(bogotaDayIsoFromMs(turn.at_ms))}` : ""}`}
          </div>
          <div className="flex flex-col gap-1.5">
            {turn.burst.map((m, k) => (
              <p key={k} className="m-0 max-w-[82%] self-start whitespace-pre-line break-words rounded-[14px] rounded-bl-[5px] bg-bubble-out px-3 py-2 text-[13.5px] leading-[1.42] text-fg">
                {m.text}
              </p>
            ))}
          </div>
          <div className="grid gap-2 min-[1100px]:grid-cols-[repeat(auto-fit,minmax(0,1fr))]">
            {arms.map((a) => (
              <CompareCell
                key={a}
                arm={a}
                turn={turn}
                thread={thread.data}
                results={resultsOf(evals[a]?.data, turn.episode_id).filter((r) => r.turn === turn.turn)}
                catalog={catalog.data}
                onOpen={() => onOpen(turn, a)}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function CompareCell({
  arm,
  turn,
  thread,
  results,
  catalog,
  onOpen,
}: {
  arm: string;
  turn: ThreadTurn;
  thread: LabThread;
  results: EvalResult[];
  catalog: CheckCatalog | undefined;
  onOpen: () => void;
}) {
  const replies = turnReplies(thread, turn, arm);
  const failing = results.filter((r) => r.verdict === "falla").map((r) => checkView(catalog, r));
  const ran = arm === PRODUCTION_ARM || !!turn.outputs[arm];
  return (
    <div role="group" aria-label={armLabel(arm)} className="grid content-start gap-1.5 rounded-lg border border-line bg-white/[0.02] p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <b className="text-[12px] font-semibold text-fg">{armLabel(arm)}</b>
        {results.length > 0 ? <VerdictBadge verdict={turnVerdict(catalog, results)} /> : null}
      </div>
      <div className="grid gap-1">
        {replies.map((r, k) =>
          r.dir === "note" || r.dir === "system" ? (
            <p key={k} className="m-0 text-[12px] italic text-fg-muted">{r.text}</p>
          ) : (
            <p
              key={k}
              className={
                "m-0 whitespace-pre-line break-words rounded-[12px] px-2.5 py-1.5 text-[12.5px] leading-[1.4] " +
                (r.dir === "comp" ? "border border-info/40 bg-info-soft text-white" : "bg-bubble-in text-white") +
                (r.byHuman ? " ring-2 ring-warn/60" : "")
              }
            >
              {r.byHuman ? <span className="mb-1 block text-[10px] font-semibold uppercase tracking-[0.06em] text-white/80">{TEAM_LABEL}</span> : null}
              {r.text || (r.hasImage ? "📷 Foto" : "")}
            </p>
          ),
        )}
      </div>
      {failing.length > 0 ? (
        <ul aria-label={`Lo que falló · ${armLabel(arm)}`} className="m-0 grid list-none gap-1 p-0 text-[11.5px]">
          {failing.map((v, k) => (
            <li key={`${v.id}:${k}`} className="flex flex-wrap items-center gap-1.5">
              <span aria-hidden="true" className={v.level === "menor" ? "text-fg-muted" : "text-danger"}>✕</span>
              <span className="text-fg-soft">{v.name}</span>
              <LevelPill level={v.level} />
            </li>
          ))}
        </ul>
      ) : null}
      {ran ? (
        <button
          type="button"
          onClick={onOpen}
          className="justify-self-start rounded-md border border-line-strong bg-white/[0.06] px-2 py-1 text-[11.5px] text-fg hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Ver cómo lo decidió
        </button>
      ) : null}
    </div>
  );
}

// ── Panel derecho: por bot y qué falló ──────────────────────────────────────

function Inspector({
  run,
  row,
  arms,
  arm,
  side,
  onSide,
  onOpenTurn,
}: {
  run: string;
  row: ConversationRow;
  arms: string[];
  arm: string;
  side: "bots" | "fallas";
  onSide: (s: "bots" | "fallas") => void;
  onOpenTurn: (turn: ThreadTurn) => void;
}) {
  const tabs: Array<["bots" | "fallas", string]> = [
    ["bots", "Por bot"],
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
        {side === "bots" ? <ByBot row={row} arms={arms} arm={arm} /> : <Failures run={run} sid={row.session_id} arm={arm} onOpenTurn={onOpenTurn} />}
      </div>
    </div>
  );
}

function ByBot({ row, arms, arm }: { row: ConversationRow; arms: string[]; arm: string }) {
  const episodes = row.verdicts[arm] ?? {};
  return (
    <>
      <h5 className={"mb-1.5 mt-3 " + EYEBROW}>Cómo le fue a cada bot en esta conversación</h5>
      <ul className="m-0 grid list-none gap-0.5 p-0">
        {arms.map((a) => {
          const v = rowVerdict(row, a);
          return (
            <li key={a} className={"flex items-center justify-between gap-2 rounded-md px-1.5 py-[5px] " + (a === arm ? "bg-white/5 text-fg" : "")}>
              <span>{armLabel(a)}</span>
              {v ? <VerdictBadge verdict={v} /> : <span className="text-[11.5px] text-fg-faint">todavía no corrió</span>}
            </li>
          );
        })}
      </ul>
      {row.episodes.length > 1 ? (
        <>
          <h5 className={"mb-1.5 mt-3 " + EYEBROW}>{`Episodios · ${armLabel(arm)}`}</h5>
          <p className="m-0 mb-1 text-[11px] text-fg-faint">Cada episodio es un intento de venta distinto dentro de la misma conversación.</p>
          {row.episodes.map((ep, k) => (
            <div key={ep} className="flex items-center justify-between gap-2 rounded-md px-1.5 py-[5px]">
              <span>{`Episodio ${k + 1}`}</span>
              {episodes[ep] ? <VerdictBadge verdict={episodes[ep]} /> : <span className="text-[11.5px] text-fg-faint">sin evaluar</span>}
            </div>
          ))}
        </>
      ) : null}
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

function Failures({ run, sid, arm, onOpenTurn }: { run: string; sid: string; arm: string; onOpenTurn: (turn: ThreadTurn) => void }) {
  const evals = useRunEvaluationsByArm(run, sid, [arm])[arm];
  const catalog = useCheckCatalog();
  const thread = useRunThread(run, sid);
  if (!evals || evals.isPending) return <p className="text-fg-muted">Cargando las evaluaciones…</p>;
  if (evals.isError) return <p className="text-fg-muted">No se pudieron leer las evaluaciones.</p>;
  const episodes = evals.data?.episodes ?? [];
  if (episodes.length === 0) return <p className="text-fg-muted">{`${armLabel(arm)} todavía no tiene evaluaciones en esta conversación.`}</p>;
  const open = (episodeId: string) => (turnNumber: number) => {
    const turn = thread.data?.turns.find((t) => t.episode_id === episodeId && t.turn === turnNumber);
    if (turn) onOpenTurn(turn);
  };
  return (
    <>
      <h5 className={"mb-2 mt-3 " + EYEBROW}>{`Qué falló · ${armLabel(arm)}`}</h5>
      {episodes.map((ep, k) => (
        <section key={ep.episode_id} aria-label={`Episodio ${k + 1}`} className="mb-3">
          {episodes.length > 1 ? (
            <h6 className={"mb-1.5 flex items-center gap-2 " + EYEBROW}>
              {`Episodio ${k + 1}`} <VerdictBadge verdict={ep.verdict} />
            </h6>
          ) : null}
          <CheckResults results={ep.results} catalog={catalog.data} scope="conversation" onOpenTurn={open(ep.episode_id)} />
        </section>
      ))}
    </>
  );
}
