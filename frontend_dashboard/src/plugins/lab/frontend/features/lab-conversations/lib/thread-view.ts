/**
 * Hilo de una conversación del banco para la sección Laboratorio (plan §11):
 * burbujas como en Chats, cada ráfaga agrupada y el botón "Ver hilo del turno"
 * al final de la respuesta de cada turno.
 *
 * - A0 (producción): los mensajes reales, en orden. Un mensaje del cliente
 *   pertenece a un turno del banco por su wamid (o por su hora exacta si el
 *   evento no trae wamid). El botón del turno sale antes del siguiente
 *   mensaje del cliente o al final.
 * - A1/B/C (simulados): por cada turno, la ráfaga del cliente y lo que ESE
 *   bot respondió (`outputs[brazo].sent_texts`); si todavía no corrió, una
 *   nota.
 *
 * El botón de cada turno lleva el resultado de la evaluación de ESE turno con
 * ese bot (`verdictOf`, el mismo que muestra el modal); sin evaluación, «sin
 * evaluar». Antes se pintaba de ámbar si el bot había descartado un texto,
 * y el color no coincidía con lo que decía el modal (revisión 2026-09-29).
 *
 * Función pura: el día sale como ISO (la etiqueta "Hoy"/"Ayer" se calcula en
 * render, que es quien mira el reloj).
 */

import { BOGOTA_TZ, bogotaDayIsoFromMs, describeTool } from "@/shared/lib";
import type { EpisodeVerdict, LabThread, ThreadMessage, ThreadTurn } from "@plugins/lab/frontend/entities/lab-run";

export type ThreadItem =
  | { type: "day"; key: string; day: string }
  | { type: "msg"; key: string; dir: "in" | "out" | "comp" | "system"; text: string; time: string; hasImage: boolean; byHuman?: boolean }
  | { type: "burst"; key: string; turn: ThreadTurn; messages: Array<{ text: string; time: string }>; spanS: number }
  | { type: "chip"; key: string; turn: ThreadTurn; verdict: EpisodeVerdict | null; sub: string }
  | { type: "note"; key: string; text: string };

/** El resultado de un turno con el bot del hilo (`null` si todavía no tiene evaluación). */
export type TurnVerdictOf = (turn: ThreadTurn) => EpisodeVerdict | null;

const NOT_EVALUATED: TurnVerdictOf = () => null;

/** Lo que respondió un bot en un turno: sus burbujas y, si no hay, por qué. */
export type ReplyItem = { dir: "out" | "comp" | "system" | "note"; text: string; hasImage: boolean; byHuman?: boolean };

export const PRODUCTION_ARM = "A0";
const NOT_RUN = "Este bot todavía no respondió este turno.";

/** Por qué un turno simulado no envió nada (`suppressed_reason` de la traza). */
const SUPPRESSED: Record<string, string> = {
  tag_closure: "cerró la conversación con una etiqueta",
  no_message: "decidió no escribir",
  admin_turn: "era un turno interno del equipo",
  admin_text_guard: "el texto parecía una nota interna y se bloqueó",
  variant_picker: "el selector de opciones es el mensaje",
  variant_enumeration_guard: "la lista de opciones se cambió por un selector",
  shutdown: "la conversación se estaba cerrando",
};

const TIME_FMT = new Intl.DateTimeFormat("es-CO", { timeZone: BOGOTA_TZ, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

function hhmm(ms: number | null): string {
  return ms === null || !Number.isFinite(ms) ? "" : TIME_FMT.format(new Date(ms)).replace(/^24:/, "00:");
}

function msOf(message: ThreadMessage): number | null {
  if (!message.timestamp) return null;
  const ms = Date.parse(message.timestamp);
  return Number.isFinite(ms) ? ms : null;
}

function dirOf(message: ThreadMessage): "in" | "out" | "comp" | "system" {
  if (message.role === "user") return "in";
  if (message.role === "system") return "system";
  return message.kind === "component" ? "comp" : "out";
}

function chip(turn: ThreadTurn, key: string, verdictOf: TurnVerdictOf): ThreadItem {
  const n = turn.burst.length;
  return { type: "chip", key, turn, verdict: verdictOf(turn), sub: `turno ${turn.turn} · ${n} ${n === 1 ? "mensaje" : "mensajes"}` };
}

function burstItem(turn: ThreadTurn, key: string): ThreadItem {
  const times = turn.burst.map((m) => m.ts_ms).filter((t): t is number => t !== null);
  const spanS = times.length > 1 ? Math.round((Math.max(...times) - Math.min(...times)) / 1000) : 0;
  return { type: "burst", key, turn, messages: turn.burst.map((m) => ({ text: m.text, time: hhmm(m.ts_ms) })), spanS };
}

function sortedTurns(thread: LabThread): ThreadTurn[] {
  return [...thread.turns].sort((a, b) => (a.at_ms ?? 0) - (b.at_ms ?? 0));
}

export function buildThreadView(thread: LabThread, arm: string, verdictOf: TurnVerdictOf = NOT_EVALUATED): ThreadItem[] {
  return arm === PRODUCTION_ARM ? productionView(thread, verdictOf) : simulatedView(thread, arm, verdictOf);
}

/** A qué turno del banco pertenece cada mensaje del cliente (por wamid o por su hora exacta). */
function turnFinder(turns: ThreadTurn[]): (m: ThreadMessage) => ThreadTurn | undefined {
  const byWamid = new Map<string, ThreadTurn>();
  const byTs = new Map<number, ThreadTurn>();
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

function productionView(thread: LabThread, verdictOf: TurnVerdictOf): ThreadItem[] {
  const turns = sortedTurns(thread);
  const turnOf = turnFinder(turns);

  const items: ThreadItem[] = [];
  let day = "";
  let open: ThreadTurn | null = null;
  const shown = new Set<string>();
  // Un turno puede cerrarse y reabrirse (un mensaje que no está en el banco
  // entre dos de su ráfaga): cada chip lleva su propia key.
  const chips = new Map<string, number>();
  const chipKey = (turn: ThreadTurn): string => {
    const n = chips.get(turn.turn_key) ?? 0;
    chips.set(turn.turn_key, n + 1);
    return n === 0 ? `chip-${turn.turn_key}` : `chip-${turn.turn_key}-${n}`;
  };

  thread.messages.forEach((m, idx) => {
    const ms = msOf(m);
    const turn = turnOf(m);
    if (m.role === "user" && open) {
      if (turn !== open) {
        items.push(chip(open, chipKey(open), verdictOf));
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
  if (open) items.push(chip(open, chipKey(open as ThreadTurn), verdictOf));
  return items;
}

/** Producción: los mensajes reales que siguieron a la ráfaga del turno, hasta que el cliente vuelve a escribir. */
function productionReplies(thread: LabThread, turn: ThreadTurn): ReplyItem[] {
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

/** Un bot simulado: sus textos, lo que mandó aparte (tarjetas, botones, formularios) y lo que le rechazaron. */
function simulatedReplies(turn: ThreadTurn, arm: string): ReplyItem[] {
  const out = turn.outputs[arm];
  if (!out) return [{ dir: "note", text: NOT_RUN, hasImage: false }];
  const replies: ReplyItem[] = out.sent_texts.map((text) => ({ dir: "out" as const, text, hasImage: false }));
  for (const call of out.tools) {
    const tool = describeTool(call);
    if (!tool.component || !tool.shown) continue;
    const what = tool.detail ? `${tool.shown} · ${tool.detail}` : tool.shown;
    replies.push(
      tool.failed
        ? { dir: "note", text: `No salió: ${what} (${tool.result.replace(/^rechazada: /, "")}).`, hasImage: false }
        : { dir: "comp", text: `🧩 ${what}`, hasImage: false },
    );
  }
  if (!replies.some((r) => r.dir !== "note")) {
    const why = out.suppressed_reason ? `: ${SUPPRESSED[out.suppressed_reason] ?? out.suppressed_reason}` : "";
    replies.unshift({ dir: "note", text: `El bot no envió nada${why}.`, hasImage: false });
  }
  return replies;
}

/** Lo que respondió un bot en un turno (producción: lo real; simulado: lo de ese bot). */
export function turnReplies(thread: LabThread, turn: ThreadTurn, arm: string): ReplyItem[] {
  return arm === PRODUCTION_ARM ? productionReplies(thread, turn) : simulatedReplies(turn, arm);
}

function simulatedView(thread: LabThread, arm: string, verdictOf: TurnVerdictOf): ThreadItem[] {
  const items: ThreadItem[] = [];
  let day = "";
  for (const turn of sortedTurns(thread)) {
    const d = turn.at_ms !== null ? bogotaDayIsoFromMs(turn.at_ms) : "";
    if (d && d !== day) {
      day = d;
      items.push({ type: "day", key: `day-${d}-${turn.turn_key}`, day: d });
    }
    if (turn.burst.length > 1) {
      items.push(burstItem(turn, `burst-${turn.turn_key}`));
    } else {
      const m = turn.burst[0];
      items.push({ type: "msg", key: `in-${turn.turn_key}`, dir: "in", text: m?.text ?? "", time: hhmm(m?.ts_ms ?? null), hasImage: false });
    }
    simulatedReplies(turn, arm).forEach((r, k) =>
      items.push(
        r.dir === "note"
          ? { type: "note", key: `note-${turn.turn_key}-${k}`, text: r.text }
          : { type: "msg", key: `out-${turn.turn_key}-${k}`, dir: r.dir, text: r.text, time: "", hasImage: r.hasImage },
      ),
    );
    items.push(chip(turn, `chip-${turn.turn_key}`, verdictOf));
  }
  return items;
}

