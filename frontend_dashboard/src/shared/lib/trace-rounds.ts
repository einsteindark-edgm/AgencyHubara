/**
 * Las rondas del modelo en el «Paso a paso» (pedido del operador,
 * 2026-09-30, laboratorio 4567 t20). Funciones puras sobre los `steps[]` de
 * la traza v2:
 *  - `roundInput`: qué le manda el bot al modelo en una ronda. Con lo que la
 *    traza guardó (`sent`, desde el 2026-09-30): en la primera, el tamaño de
 *    las instrucciones, el historial, las notas del turno y el mensaje del
 *    cliente como lo lee el modelo; en las siguientes, solo lo nuevo. En una
 *    traza anterior, lo que se puede reconstruir de ella (el mensaje como lo
 *    escribió el cliente, los resultados de las herramientas y las notas del
 *    bot).
 *  - `modelRound`: lo que pidió el modelo, cada pedido con su ejecución, y lo
 *    que pasó después dentro de esa ronda (protecciones, cortes).
 * Una ronda va de un paso `llm` al siguiente.
 */

import type { TraceStep } from "./sequence-layout";

export interface RequestedCall {
  name: string;
  /** El paso que ejecutó ese pedido; null si la traza no lo trae. */
  step: TraceStep | null;
  /** El corte no lo dejó correr, con los argumentos que guardó (vacíos en
   *  una traza anterior al 2026-10-09). */
  skipped?: { args: Record<string, unknown> };
}

/** Lo que el modelo pidió y un corte no dejó correr. */
export interface SkippedCall {
  name: string;
  args: Record<string, unknown>;
}

export interface ModelRound {
  calls: RequestedCall[];
  /** Protecciones, cortes y reinicios de la ronda, en orden. */
  after: TraceStep[];
}

export interface RoundInputItem {
  kind: "customer" | "tool" | "note";
  /** El nombre de la herramienta, en un resultado. */
  name?: string;
  text: string;
}

export interface RoundInputView {
  /** true: es lo que recibió el modelo (lo guardó la traza); false: reconstruido. */
  exact: boolean;
  first: boolean;
  system?: { chars: number; parts: Array<{ name: string; chars: number }> };
  history?: Record<string, number>;
  notes?: string;
  items: RoundInputItem[];
}

/** La nota que el bot le deja al modelo cuando una protección le pide otra ronda. */
const ROUND_NOTES: Record<string, (after: string) => string> = {
  contract_extra_round: (after) => `Tu send_reply NO se envió. ${after}`,
  promised_action_round: (after) => `Tu send_reply NO se envió. ${after}`,
  turn_policy_extra_round: (after) => after,
  send_reply_retry: () =>
    "Tu send_reply NO se envió: otra herramienta del mismo paso falló. Lee el error y vuelve a responderle al cliente con send_reply.",
};

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function obj(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function nextLlm(steps: TraceStep[], from: number): number {
  const k = steps.findIndex((s, i) => i > from && s.kind === "llm");
  return k === -1 ? steps.length : k;
}

function previousLlm(steps: TraceStep[], before: number): number {
  for (let i = before - 1; i >= 0; i -= 1) if (steps[i].kind === "llm") return i;
  return -1;
}

function requestedNames(step: TraceStep | undefined): string[] {
  return Array.isArray(step?.tool_calls) ? step.tool_calls.filter((n): n is string => typeof n === "string") : [];
}

/** El corte que no deja correr lo pedido: el cliente escribió mientras el
 *  modelo pensaba (Checkpoint A, ANTES de ejecutar el paso). Los demás cortes
 *  pasan después de las herramientas. */
const CUT_BEFORE_TOOLS = "checkpoint_a";

/**
 * Lo que el modelo pidió y el corte `cutIndex` no dejó correr (turno 1 de
 * …7392, 2026-10-08: `present_products` no se veía en el diagrama). Desde el
 * 2026-10-09 el corte lo guarda con sus argumentos (`skipped`); en una traza
 * anterior sale de la ronda del modelo que lo precede, sin argumentos.
 */
export function skippedByCut(steps: TraceStep[], cutIndex: number): SkippedCall[] {
  const cut = steps[cutIndex];
  if (cut?.kind !== "cut" || cut.reason !== CUT_BEFORE_TOOLS) return [];
  if (Array.isArray(cut.skipped)) {
    return cut.skipped.map(obj).filter((c) => str(c.name)).map((c) => ({ name: str(c.name), args: obj(c.args) }));
  }
  const llm = previousLlm(steps, cutIndex);
  if (llm === -1 || steps.slice(llm + 1, cutIndex).some((s) => s.kind === "tool")) return [];
  return requestedNames(steps[llm]).map((name) => ({ name, args: {} }));
}

export function modelRound(steps: TraceStep[], stepIndex: number): ModelRound {
  const end = nextLlm(steps, stepIndex);
  const rest = steps.slice(stepIndex + 1, end);
  const tools = rest.filter((s) => s.kind === "tool");
  const cutAt = steps.findIndex((s, i) => i > stepIndex && i < end && s.kind === "cut");
  const skipped = cutAt === -1 ? [] : skippedByCut(steps, cutAt);
  const used = new Set<TraceStep>();
  const usedSkips = new Set<SkippedCall>();
  const calls = requestedNames(steps[stepIndex]).map((name): RequestedCall => {
    const found = tools.find((t) => t.name === name && !used.has(t)) ?? null;
    if (found) {
      used.add(found);
      return { name, step: found };
    }
    const skip = skipped.find((c) => c.name === name && !usedSkips.has(c));
    if (!skip) return { name, step: null };
    usedSkips.add(skip);
    return { name, step: null, skipped: { args: skip.args } };
  });
  return { calls, after: rest.filter((s) => s.kind === "guard" || s.kind === "cut" || s.kind === "restart") };
}

function sentItem(entry: Record<string, unknown>): RoundInputItem | null {
  const text = str(entry.text);
  switch (entry.role) {
    case "user":
      return { kind: "customer", text };
    case "tool":
      return { kind: "tool", name: str(entry.name), text };
    case "system":
      return { kind: "note", text };
    default:
      return null;
  }
}

export function roundInput(steps: TraceStep[], stepIndex: number): RoundInputView {
  const prev = previousLlm(steps, stepIndex);
  const first = prev === -1;
  const sent = obj(steps[stepIndex]?.sent);
  if (Array.isArray(sent.new)) {
    const items = sent.new.map(obj).map(sentItem).filter((it): it is RoundInputItem => it !== null);
    if (!first) return { exact: true, first, items };
    const system = obj(sent.system);
    const history = obj(sent.history);
    return {
      exact: true,
      first,
      system: {
        chars: typeof system.chars === "number" ? system.chars : 0,
        parts: (Array.isArray(system.parts) ? system.parts : []).map(obj).map((p) => ({ name: str(p.name), chars: typeof p.chars === "number" ? p.chars : 0 })),
      },
      history: Object.fromEntries(Object.entries(history).filter((e): e is [string, number] => typeof e[1] === "number")),
      notes: str(sent.notes),
      items,
    };
  }
  if (first) {
    const inbound = steps.slice(0, stepIndex).find((s) => s.kind === "inbound");
    const lines = (Array.isArray(inbound?.messages) ? inbound.messages : []).map(obj).map((m) => str(m.text)).filter(Boolean);
    return { exact: false, first, items: lines.length ? [{ kind: "customer", text: lines.join("\n") }] : [] };
  }
  const items: RoundInputItem[] = [];
  for (const s of steps.slice(prev + 1, stepIndex)) {
    if (s.kind === "tool") items.push({ kind: "tool", name: str(s.name), text: str(s.excerpt) });
    const note = s.kind === "guard" ? ROUND_NOTES[str(s.name)] : undefined;
    if (note) items.push({ kind: "note", text: note(str(s.after)) });
  }
  return { exact: false, first, items };
}
