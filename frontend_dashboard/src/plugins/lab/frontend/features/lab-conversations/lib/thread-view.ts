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
 * La vista de producción es la de `@/shared/lib` (`quality-thread.ts`, la
 * misma de Calidad LLM de Agents). Función pura: el día sale como ISO (la etiqueta "Hoy"/"Ayer" se calcula en
 * render, que es quien mira el reloj).
 */

import {
  bogotaDayIsoFromMs,
  burstItem,
  describeTool,
  hhmm,
  productionReplies,
  productionThreadView,
  sortedTurns,
  turnChip,
  type ReplyItem as SharedReplyItem,
  type ThreadItem as SharedThreadItem,
  type TurnVerdictOf as SharedTurnVerdictOf,
} from "@/shared/lib";
import type { LabThread, ThreadTurn } from "@plugins/lab/frontend/entities/lab-run";

export type ThreadItem = SharedThreadItem<ThreadTurn>;

/** El resultado de un turno con el bot del hilo (`null` si todavía no tiene evaluación). */
export type TurnVerdictOf = SharedTurnVerdictOf<ThreadTurn>;

const NOT_EVALUATED: TurnVerdictOf = () => null;

/** Lo que respondió un bot en un turno: sus burbujas y, si no hay, por qué. */
export type ReplyItem = SharedReplyItem;

/** El texto que acompañó a un componente (intro de la lista, cuerpo de los botones). */
function componentIntro(args: unknown): string | null {
  const a = args && typeof args === "object" && !Array.isArray(args) ? (args as Record<string, unknown>) : {};
  for (const key of ["intro_text", "body_text", "body"]) {
    const v = a[key];
    if (typeof v === "string" && v.trim() !== "") return v.trim();
  }
  return null;
}

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

export function buildThreadView(thread: LabThread, arm: string, verdictOf: TurnVerdictOf = NOT_EVALUATED): ThreadItem[] {
  return arm === PRODUCTION_ARM ? productionThreadView(thread, verdictOf) : simulatedView(thread, arm, verdictOf);
}

/** Un bot simulado: sus textos, lo que mandó aparte (tarjetas, botones, formularios) y lo que le rechazaron. */
function simulatedReplies(turn: ThreadTurn, arm: string): ReplyItem[] {
  const out = turn.outputs[arm];
  if (!out) return [{ dir: "note", text: NOT_RUN, hasImage: false }];
  const replies: ReplyItem[] = out.sent_texts.map((text) => ({ dir: "out" as const, text, hasImage: false }));
  // La protección cambió la lista que escribió el bot por el selector: el
  // cliente recibió ESE texto (lo de antes y después de la lista, y las opciones).
  if (out.selector_text) {
    replies.push({ dir: "comp", text: `🧩 Selector de opciones (armado con la lista que escribió el bot)\n${out.selector_text}`, hasImage: false });
  }
  for (const call of out.tools) {
    const tool = describeTool(call);
    if (!tool.component || !tool.shown) continue;
    const what = tool.detail ? `${tool.shown} · ${tool.detail}` : tool.shown;
    const intro = componentIntro(call.args);
    replies.push(
      tool.failed
        ? { dir: "note", text: `No salió: ${what} (${tool.result.replace(/^rechazada: /, "")}).`, hasImage: false }
        : { dir: "comp", text: `🧩 ${what}${intro ? `\n«${intro}»` : ""}`, hasImage: false },
    );
  }
  if (!replies.some((r) => r.dir !== "note")) {
    const why = out.suppressed_reason ? `: ${SUPPRESSED[out.suppressed_reason] ?? out.suppressed_reason}` : "";
    replies.unshift({ dir: "note", text: `El bot no envió nada${why}.`, hasImage: false });
  }
  if (out.complement_texts.length > 0) {
    replies.push({ dir: "note", text: "Después mandó un mensaje de complemento (Jev notó que faltaba algo):", hasImage: false });
    out.complement_texts.forEach((text) => replies.push({ dir: "out", text, hasImage: false, complement: true }));
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
  for (const turn of sortedTurns(thread.turns)) {
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
          : { type: "msg", key: `out-${turn.turn_key}-${k}`, dir: r.dir, text: r.text, time: "", hasImage: r.hasImage, complement: r.complement },
      ),
    );
    items.push(turnChip(turn, `chip-${turn.turn_key}`, verdictOf));
  }
  return items;
}

