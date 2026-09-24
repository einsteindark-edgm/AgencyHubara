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
 * Función pura: el día sale como ISO (la etiqueta "Hoy"/"Ayer" se calcula en
 * render, que es quien mira el reloj).
 */

import { BOGOTA_TZ, bogotaDayIsoFromMs } from "@/shared/lib";
import type { LabThread, ThreadMessage, ThreadTurn } from "@plugins/lab/frontend/entities/lab-run";

export type ThreadItem =
  | { type: "day"; key: string; day: string }
  | { type: "msg"; key: string; dir: "in" | "out" | "comp" | "system"; text: string; time: string; hasImage: boolean }
  | { type: "burst"; key: string; turn: ThreadTurn; messages: Array<{ text: string; time: string }>; spanS: number }
  | { type: "chip"; key: string; turn: ThreadTurn; tone: "warn" | "neutral"; sub: string }
  | { type: "note"; key: string; text: string };

export const PRODUCTION_ARM = "A0";
const NOT_RUN = "Este bot todavía no respondió este turno.";

const TIME_FMT = new Intl.DateTimeFormat("es-CO", { timeZone: BOGOTA_TZ, hour: "2-digit", minute: "2-digit", hour12: false });

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

function chip(turn: ThreadTurn, arm: string, key: string): ThreadItem {
  const out = turn.outputs[arm];
  const warn = !!out && (out.discarded_narration.length > 0 || !!out.suppressed_reason);
  const n = turn.burst.length;
  return { type: "chip", key, turn, tone: warn ? "warn" : "neutral", sub: `turno ${turn.turn} · ${n} ${n === 1 ? "mensaje" : "mensajes"}` };
}

function burstItem(turn: ThreadTurn, key: string): ThreadItem {
  const times = turn.burst.map((m) => m.ts_ms).filter((t): t is number => t !== null);
  const spanS = times.length > 1 ? Math.round((Math.max(...times) - Math.min(...times)) / 1000) : 0;
  return { type: "burst", key, turn, messages: turn.burst.map((m) => ({ text: m.text, time: hhmm(m.ts_ms) })), spanS };
}

function sortedTurns(thread: LabThread): ThreadTurn[] {
  return [...thread.turns].sort((a, b) => (a.at_ms ?? 0) - (b.at_ms ?? 0));
}

export function buildThreadView(thread: LabThread, arm: string): ThreadItem[] {
  return arm === PRODUCTION_ARM ? productionView(thread) : simulatedView(thread, arm);
}

function productionView(thread: LabThread): ThreadItem[] {
  const turns = sortedTurns(thread);
  const byWamid = new Map<string, ThreadTurn>();
  const byTs = new Map<number, ThreadTurn>();
  for (const turn of turns) {
    for (const m of turn.burst) {
      if (m.wamid) byWamid.set(m.wamid, turn);
      if (m.ts_ms !== null) byTs.set(m.ts_ms, turn);
    }
  }
  const turnOf = (m: ThreadMessage): ThreadTurn | undefined => {
    if (m.role !== "user") return undefined;
    if (m.wamid && byWamid.has(m.wamid)) return byWamid.get(m.wamid);
    const ms = msOf(m);
    return ms !== null ? byTs.get(ms) : undefined;
  };

  const items: ThreadItem[] = [];
  let day = "";
  let open: ThreadTurn | null = null;
  const shown = new Set<string>();

  thread.messages.forEach((m, idx) => {
    const ms = msOf(m);
    const turn = turnOf(m);
    if (m.role === "user" && open) {
      if (turn !== open) {
        items.push(chip(open, PRODUCTION_ARM, `chip-${open.turn_key}`));
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
    items.push({ type: "msg", key: `m-${idx}`, dir: dirOf(m), text: m.content, time: hhmm(ms), hasImage: m.has_image });
  });
  if (open) items.push(chip(open, PRODUCTION_ARM, `chip-${(open as ThreadTurn).turn_key}`));
  return items;
}

function simulatedView(thread: LabThread, arm: string): ThreadItem[] {
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
    const out = turn.outputs[arm];
    if (!out) {
      items.push({ type: "note", key: `note-${turn.turn_key}`, text: NOT_RUN });
    } else if (out.sent_texts.length === 0) {
      const why = out.suppressed_reason ? ` (${out.suppressed_reason})` : "";
      items.push({ type: "note", key: `note-${turn.turn_key}`, text: `El bot no envió nada${why}.` });
    } else {
      out.sent_texts.forEach((text, k) => items.push({ type: "msg", key: `out-${turn.turn_key}-${k}`, dir: "out", text, time: "", hasImage: false }));
    }
    items.push(chip(turn, arm, `chip-${turn.turn_key}`));
  }
  return items;
}
