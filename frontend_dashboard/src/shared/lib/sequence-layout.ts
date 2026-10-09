/**
 * Diagrama de secuencia del hilo de un turno — función pura (plan del
 * laboratorio §11.1–§11.2, diseño aprobado el 2026-09-23).
 *
 * Recibe los `steps[]` de la traza v2 del bot de ventas y devuelve una fila por
 * flecha (o caja dentro de un carril), con la geometría del diseño: carriles en
 * x = 58, 173, 288, 403 y 518 sobre un ancho de 610; primer paso en y = 70 y
 * 44 px entre pasos.
 *
 * Carriles, en palabras del operador (revisión 2026-09-29): Cliente · Bot
 * (el workflow) · Jev (el clasificador) · Modelo de IA (el LLM) ·
 * Herramientas (las tools).
 *  - `inbound`: Cliente → Bot.
 *  - `perception` y `verify`: Bot → Jev y de vuelta.
 *  - `llm`: Bot → Modelo ("ronda N") y Modelo → Bot (lo que pidió).
 *  - `tool`: Bot → Herramientas y de vuelta: las tools las ejecuta el BOT
 *    (`execute_tool` de exoclaw) después de que el modelo las pide.
 *  - `plan`, `guard`, `cut`, `restart`: una caja dentro del Bot.
 *  - `outbound`: Bot → Cliente.
 * Las tools se nombran por lo que hacen (`sales-tools.ts`). La flecha hacia
 * las herramientas dice cuál se ejecutó (operador, 2026-09-30): lo que pidió el
 * modelo ya va en palabras en la fila anterior.
 */

import { describeTool, toolActionPhrase } from "./sales-tools";

export type TraceStep = {
  i?: number;
  at_ms: number | null;
  kind: string;
  dur_ms?: number | null;
  [key: string]: unknown;
};

export type StepStatus = "info" | "classifier" | "tool" | "ok" | "warn" | "bad" | "neutral";

export type SeqRow = {
  /** Posición de la fila en el dibujo (0…n-1). */
  index: number;
  /** Índice del paso de la traza del que sale (una llamada da dos filas). */
  stepIndex: number;
  from: number;
  to: number;
  status: StepStatus;
  /** Etiqueta corta sobre la flecha. */
  short: string;
  /** Nombre de la herramienta que se ejecutó (la etiqueta empieza con él). */
  code?: string;
  /** Título del detalle ("N. título"). */
  title: string;
  /** Tipo del paso, en el color del paso. */
  kind: string;
  /** Tiempo desde el inicio del turno ("+2,5 s"); vacío si la traza no lo trae. */
  t: string;
  /** Duración ("0,3 s") de la llamada, en la fila de ida. */
  dur: string | null;
  y: number;
  /** Respuesta hacia la izquierda que no llega al cliente. */
  dashed: boolean;
};

export type SequenceLayout = {
  lanes: string[];
  lanesX: number[];
  rows: SeqRow[];
  width: number;
  height: number;
  usesClassifier: boolean;
};

/** Color de cada estado de paso: tokens del tema (`--color-*`). */
export const STEP_COLOR: Record<StepStatus, string> = {
  info: "var(--color-info)",
  classifier: "var(--color-violet)",
  tool: "var(--color-cyan)",
  ok: "var(--color-ok)",
  warn: "var(--color-warn)",
  bad: "var(--color-danger)",
  neutral: "var(--color-neutral)",
};

export const LANE_X = [58, 173, 288, 403, 518];
export const SEQ_WIDTH = 610;
export const SEQ_Y0 = 70;
export const SEQ_DY = 44;

const CLIENT = 0;
const WORKFLOW = 1;
const CLASSIFIER = 2;
const LLM = 3;
const TOOLS = 4;

const CUT_LABELS: Record<string, string> = {
  awaits_customer: "espera al cliente",
  escalation: "pasa a una persona",
  send_reply: "respuesta entregada",
  tag_closure: "cierra la conversación",
  checkpoint_a: "el cliente escribió mientras pensaba",
  checkpoint_b: "el cliente escribió: el cierre no se envía",
  before_record: "el cliente siguió escribiendo antes del envío",
};

/** Lo que hizo cada guarda con el texto del modelo. */
export const GUARD_LABELS: Record<string, string> = {
  variant_enumeration_guard: "cambió una lista de opciones por un selector",
  apply_variant_enumeration_guard: "cambió una lista de opciones por un selector",
  portavelas_notice_guard: "quitó el aviso de portavelas",
  admin_text_guard: "bloqueó un texto interno",
  admin_text_salvaged: "rescató el texto para el cliente",
  variant_picker_text: "el selector de opciones reemplazó el texto",
  first_contact_greeting: "agregó la bienvenida del primer contacto",
  self_transfer_noop: "no mandó el texto de una autotransferencia",
  safety_net_closing_escalation: "escaló a una persona al cerrar (red de seguridad)",
  safety_net_promised_handoff: "escaló a una persona: el mensaje prometía que un colega lo atiende",
  contract_extra_round: "retuvo la respuesta: antes debía consultar una herramienta",
  turn_policy_extra_round: "pidió una ronda más por un asunto pendiente",
  send_reply_retry: "no mandó la respuesta: otra herramienta del mismo paso falló",
  promised_action_round: "retuvo la respuesta: prometía algo que todavía no hizo",
  promised_action_text_kept: "mandó la respuesta retenida: lo prometido ya salió",
};

/** Lo que decidió Jev al revisar si la respuesta cubre cada asunto. */
const VERIFY_DECISIONS: Record<string, string> = {
  send: "enviar",
  complement: "enviar y completar después",
  pending: "pendiente",
};

type Draft = Omit<SeqRow, "index" | "y" | "dashed">;

function seconds(ms: number): string {
  return (ms / 1000).toFixed(1).replace(".", ",");
}

function at(ms: number | null | undefined, plus = 0): string {
  return typeof ms === "number" ? `+${seconds(ms + plus)} s` : "";
}

function duration(step: TraceStep): string | null {
  return typeof step.dur_ms === "number" ? `${seconds(step.dur_ms)} s` : null;
}

function names(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function count(value: unknown): number {
  return Array.isArray(value) ? value.length : 0;
}

function textDiscarded(step: TraceStep): boolean {
  return typeof step.text_fate === "string" && step.text_fate.startsWith("discarded");
}

function outboundStatus(step: TraceStep): StepStatus {
  const bubbles = Array.isArray(step.bubbles) ? step.bubbles : [];
  const delivered = bubbles.map((b) => (b && typeof b === "object" ? (b as { delivered?: unknown }).delivered : null));
  if (delivered.length === 0 || delivered.some((d) => d !== true && d !== false)) return "neutral";
  if (delivered.every((d) => d === true)) return "ok";
  if (delivered.every((d) => d === false)) return "bad";
  return "warn";
}

function rowsFor(step: TraceStep, stepIndex: number): Draft[] {
  const base = { stepIndex, dur: null as string | null };
  const time = at(step.at_ms);
  const back = at(step.at_ms, typeof step.dur_ms === "number" ? step.dur_ms : 0);
  switch (step.kind) {
    case "inbound": {
      const n = count(step.messages);
      const short = n > 1 ? `${n} mensajes seguidos` : "1 mensaje";
      const title = n > 1 ? `El cliente escribió ${n} mensajes seguidos` : "Mensaje del cliente";
      return [{ ...base, from: CLIENT, to: WORKFLOW, status: "info", short, title, kind: "Cliente", t: time }];
    }
    case "perception":
    case "verify": {
      const verify = step.kind === "verify";
      const kind = verify ? "Jev · revisión" : "Jev · lectura";
      const n = count(step.answers);
      const decision = String(step.decision ?? "—");
      const decided = `decisión: ${VERIFY_DECISIONS[decision] ?? decision}`;
      return [
        {
          ...base,
          dur: duration(step),
          from: WORKFLOW,
          to: CLASSIFIER,
          status: "classifier",
          short: verify ? "¿respondió cada asunto?" : `lee el mensaje · ${n} ${n === 1 ? "pregunta" : "preguntas"}`,
          title: verify ? "Jev revisa si la respuesta cubre cada asunto" : "Jev lee el mensaje",
          kind,
          t: time,
        },
        { ...base, from: CLASSIFIER, to: WORKFLOW, status: "classifier", short: verify ? decided : "responde", title: verify ? decided.charAt(0).toUpperCase() + decided.slice(1) : "Respuestas de Jev", kind, t: back },
      ];
    }
    case "plan":
      return [{ ...base, from: WORKFLOW, to: WORKFLOW, status: "neutral", short: `plan: ${count(step.checklist)} asuntos`, title: `Plan: ${count(step.checklist)} asuntos por responder`, kind: "Plan", t: time }];
    case "llm": {
      const round = typeof step.round === "number" ? step.round : 1;
      const asked = names(step.tool_calls);
      const withText = textDiscarded(step) || step.text_fate === "pre_tool_message";
      const short = asked.length
        ? `pide ${toolActionPhrase(asked[0])}${asked.length > 1 ? ` +${asked.length - 1}` : ""}${withText ? " + texto" : ""}`
        : "escribe el texto final";
      const title = asked.length ? `Pide ${asked.map(toolActionPhrase).join(", ")}` : "Escribe el texto final";
      return [
        { ...base, dur: duration(step), from: WORKFLOW, to: LLM, status: "info", short: `ronda ${round}`, title: `Ronda ${round} del modelo`, kind: "Modelo de IA", t: time },
        { ...base, from: LLM, to: WORKFLOW, status: textDiscarded(step) ? "warn" : "info", short, title, kind: "Modelo de IA", t: back },
      ];
    }
    case "tool": {
      const name = typeof step.name === "string" ? step.name : "tool";
      const tool = describeTool({ name, args: step.args, ok: step.ok as boolean | null, error: step.error as string | null, notes: step.notes });
      const title = tool.detail ? `${tool.action} · ${tool.detail}` : tool.action;
      const short = tool.detail ? `${name} · ${tool.detail}` : name;
      return [
        { ...base, dur: duration(step), from: WORKFLOW, to: TOOLS, status: "tool", short, code: name, title, kind: "Herramienta", t: time },
        { ...base, from: TOOLS, to: WORKFLOW, status: tool.failed ? "bad" : "tool", short: tool.result, title: `Resultado: ${tool.result}`, kind: "Herramienta", t: back },
      ];
    }
    case "guard": {
      const name = typeof step.name === "string" ? step.name : "guarda";
      const label = GUARD_LABELS[name] ?? name;
      const removed = typeof step.before === "string" && step.before !== "" && step.after === "";
      return [{ ...base, from: WORKFLOW, to: WORKFLOW, status: removed ? "bad" : "warn", short: label, title: `Protección: ${label}`, kind: "Protección", t: time }];
    }
    case "cut": {
      const reason = typeof step.reason === "string" ? step.reason : "";
      const label = CUT_LABELS[reason] ?? (reason || "fin del turno");
      return [{ ...base, from: WORKFLOW, to: WORKFLOW, status: "neutral", short: label, title: `Fin del turno: ${label}`, kind: "Fin del turno", t: time }];
    }
    case "restart": {
      const attempt = typeof step.attempt === "number" ? step.attempt : 1;
      const drained = typeof step.drained === "number" ? step.drained : 0;
      // Texto antes de la foto (2026-09-30): el turno esperó la foto que se leía.
      const label = step.photo === true ? "vuelve a empezar · esperó la foto" : `vuelve a empezar · +${drained} mensaje${drained === 1 ? "" : "s"}`;
      return [{ ...base, from: WORKFLOW, to: WORKFLOW, status: "warn", short: label, title: `Vuelve a empezar (${attempt})`, kind: "Reinicio", t: time }];
    }
    case "outbound": {
      const n = count(step.bubbles);
      return [{ ...base, from: WORKFLOW, to: CLIENT, status: outboundStatus(step), short: `envía ${n} ${n === 1 ? "mensaje" : "mensajes"}`, title: `Envía ${n} ${n === 1 ? "mensaje" : "mensajes"} al cliente`, kind: "Envío", t: time }];
    }
    default:
      return [{ ...base, from: WORKFLOW, to: WORKFLOW, status: "neutral", short: step.kind, title: step.kind, kind: step.kind, t: time }];
  }
}

export function layoutSequence(steps: TraceStep[], opts: { classifierLabel?: string } = {}): SequenceLayout {
  const drafts = steps.flatMap((step, i) => rowsFor(step, i));
  const rows: SeqRow[] = drafts.map((d, index) => ({
    ...d,
    index,
    y: SEQ_Y0 + index * SEQ_DY,
    dashed: d.to < d.from && d.to !== CLIENT,
  }));
  return {
    lanes: ["Cliente", "Bot", opts.classifierLabel ?? "Jev", "Modelo de IA", "Herramientas"],
    lanesX: LANE_X,
    rows,
    width: SEQ_WIDTH,
    height: SEQ_Y0 + Math.max(rows.length - 1, 0) * SEQ_DY + 34,
    usesClassifier: rows.some((r) => r.from === CLASSIFIER || r.to === CLASSIFIER),
  };
}
