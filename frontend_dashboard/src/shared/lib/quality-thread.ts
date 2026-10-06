/**
 * El hilo de una conversación real con cada turno del bot (laboratorio §11 y
 * Calidad LLM de Agents, 2026-10-02): burbujas como en Chats, cada ráfaga
 * agrupada y el botón «Ver hilo del turno» al final de la respuesta de cada
 * turno, con el resultado de ESE turno (`verdictOf`, el mismo que muestra la
 * ventana del turno; sin evaluación, «sin evaluar»).
 *
 * Los mensajes son los reales, en orden. Un mensaje del cliente pertenece a
 * un turno por su wamid (o por su hora exacta si el evento no trae wamid). El
 * botón del turno sale antes del siguiente mensaje del cliente o al final.
 *
 * Funciones puras: el día sale como ISO (la etiqueta «Hoy»/«Ayer» se calcula
 * en render, que es quien mira el reloj).
 */

import { BOGOTA_TZ, bogotaDayIsoFromMs } from "./dates";
import type { QualityVerdict } from "./quality-view";

export interface ThreadMessageView {
  role: string;
  content: string;
  timestamp: string | null;
  sender: string | null;
  kind: string | null;
  wamid: string | null;
  has_image: boolean;
}

export interface BurstMessageView {
  text: string;
  ts_ms: number | null;
  wamid: string | null;
}

export interface ThreadTurnView {
  turn_key: string;
  episode_id: string;
  turn: number;
  at_ms: number | null;
  burst: BurstMessageView[];
}

export interface ThreadView<T extends ThreadTurnView = ThreadTurnView> {
  messages: ThreadMessageView[];
  turns: T[];
}

export type ThreadItem<T extends ThreadTurnView = ThreadTurnView> =
  | { type: "day"; key: string; day: string }
  | { type: "msg"; key: string; dir: "in" | "out" | "comp" | "system"; text: string; time: string; hasImage: boolean; byHuman?: boolean; complement?: boolean }
  | { type: "burst"; key: string; turn: T; messages: Array<{ text: string; time: string }>; spanS: number }
  | { type: "chip"; key: string; turn: T; verdict: QualityVerdict | null; sub: string }
  | { type: "note"; key: string; text: string };

/** El resultado de un turno (`null` si todavía no tiene evaluación). */
export type TurnVerdictOf<T extends ThreadTurnView = ThreadTurnView> = (turn: T) => QualityVerdict | null;

/** Lo que respondió el bot en un turno: sus burbujas y, si no hay, por qué. */
export type ReplyItem = { dir: "out" | "comp" | "system" | "note"; text: string; hasImage: boolean; byHuman?: boolean; complement?: boolean };

const NOT_EVALUATED = () => null;

const TIME_FMT = new Intl.DateTimeFormat("es-CO", { timeZone: BOGOTA_TZ, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

export function hhmm(ms: number | null): string {
  return ms === null || !Number.isFinite(ms) ? "" : TIME_FMT.format(new Date(ms)).replace(/^24:/, "00:");
}

function msOf(message: ThreadMessageView): number | null {
  if (!message.timestamp) return null;
  const ms = Date.parse(message.timestamp);
  return Number.isFinite(ms) ? ms : null;
}

function dirOf(message: ThreadMessageView): "in" | "out" | "comp" | "system" {
  if (message.role === "user") return "in";
  if (message.role === "system") return "system";
  return message.kind === "component" ? "comp" : "out";
}

export function turnChip<T extends ThreadTurnView>(turn: T, key: string, verdictOf: TurnVerdictOf<T>): ThreadItem<T> {
  const n = turn.burst.length;
  return { type: "chip", key, turn, verdict: verdictOf(turn), sub: `turno ${turn.turn} · ${n} ${n === 1 ? "mensaje" : "mensajes"}` };
}

export function burstItem<T extends ThreadTurnView>(turn: T, key: string): ThreadItem<T> {
  const times = turn.burst.map((m) => m.ts_ms).filter((t): t is number => t !== null);
  const spanS = times.length > 1 ? Math.round((Math.max(...times) - Math.min(...times)) / 1000) : 0;
  return { type: "burst", key, turn, messages: turn.burst.map((m) => ({ text: m.text, time: hhmm(m.ts_ms) })), spanS };
}

export function sortedTurns<T extends ThreadTurnView>(turns: readonly T[]): T[] {
  return [...turns].sort((a, b) => (a.at_ms ?? 0) - (b.at_ms ?? 0));
}

/** A qué turno pertenece cada mensaje del cliente (por wamid o por su hora exacta). */
function turnFinder<T extends ThreadTurnView>(turns: readonly T[]): (m: ThreadMessageView) => T | undefined {
  const byWamid = new Map<string, T>();
  const byTs = new Map<number, T>();
  for (const turn of turns) {
    for (const m of turn.burst) {
      if (m.wamid) byWamid.set(m.wamid, turn);
      if (m.ts_ms !== null) byTs.set(m.ts_ms, turn);
    }
  }
  return (m) => {
    if (m.role !== "user") return undefined;
    if (m.wamid && byWamid.has(m.wamid)) return byWamid.get(m.wamid);
    const ms = msOf(m);
    return ms !== null ? byTs.get(ms) : undefined;
  };
}

/** El hilo real: los mensajes en orden, con la ráfaga y el botón de cada turno. */
export function productionThreadView<T extends ThreadTurnView>(
  thread: ThreadView<T>,
  verdictOf: TurnVerdictOf<T> = NOT_EVALUATED,
): ThreadItem<T>[] {
  const turns = sortedTurns(thread.turns);
  const turnOf = turnFinder(turns);

  const items: ThreadItem<T>[] = [];
  let day = "";
  let open: T | null = null;
  const shown = new Set<string>();
  // Un turno puede cerrarse y reabrirse (un mensaje que no está en el hilo
  // entre dos de su ráfaga): cada chip lleva su propia key.
  const chips = new Map<string, number>();
  const chipKey = (turn: T): string => {
    const n = chips.get(turn.turn_key) ?? 0;
    chips.set(turn.turn_key, n + 1);
    return n === 0 ? `chip-${turn.turn_key}` : `chip-${turn.turn_key}-${n}`;
  };

  thread.messages.forEach((m, idx) => {
    const ms = msOf(m);
    const turn = turnOf(m);
    if (m.role === "user" && open) {
      if (turn !== open) {
        items.push(turnChip(open, chipKey(open), verdictOf));
        open = null;
      }
    }
    const d = ms !== null ? bogotaDayIsoFromMs(ms) : "";
    if (d && d !== day) {
      day = d;
      items.push({ type: "day", key: `day-${d}-${idx}`, day: d });
    }
    if (turn) {
      if (!shown.has(turn.turn_key)) {
        shown.add(turn.turn_key);
        items.push(
          turn.burst.length > 1
            ? burstItem(turn, `burst-${turn.turn_key}`)
            : { type: "msg", key: `m-${idx}`, dir: "in", text: m.content, time: hhmm(ms), hasImage: m.has_image },
        );
      }
      open = turn;
      return;
    }
    items.push({ type: "msg", key: `m-${idx}`, dir: dirOf(m), text: m.content, time: hhmm(ms), hasImage: m.has_image, byHuman: m.sender === "human" });
  });
  if (open) items.push(turnChip(open, chipKey(open as T), verdictOf));
  return items;
}

/** Los mensajes reales que siguieron a la ráfaga del turno, hasta que el cliente vuelve a escribir. */
export function productionReplies<T extends ThreadTurnView>(thread: ThreadView<T>, turn: T): ReplyItem[] {
  const turnOf = turnFinder(thread.turns);
  const replies: ReplyItem[] = [];
  let open = false;
  let seen = false;
  let wroteAgain = false;
  for (const m of thread.messages) {
    if (m.role === "user") {
      const mine = turnOf(m)?.turn_key === turn.turn_key;
      if (seen && !mine && replies.length === 0) wroteAgain = true;
      seen = seen || mine;
      open = mine;
      continue;
    }
    if (open) {
      const dir = dirOf(m);
      replies.push({ dir: dir === "in" ? "out" : dir, text: m.content, hasImage: m.has_image, byHuman: m.sender === "human" });
    }
  }
  if (replies.length === 0) {
    return [
      {
        dir: "note",
        text: wroteAgain
          ? "El cliente volvió a escribir antes de que el bot respondiera: la respuesta salió con el turno siguiente."
          : "El bot no respondió este turno.",
        hasImage: false,
      },
    ];
  }
  return replies;
}
