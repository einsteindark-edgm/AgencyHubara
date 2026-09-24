/**
 * Diagrama de secuencia del hilo de un turno — función pura (plan del
 * laboratorio §11.1–§11.2, diseño aprobado el 2026-09-23).
 *
 * Recibe los `steps[]` de la traza v2 del bot de ventas y devuelve una fila por
 * flecha (o caja dentro de un carril), con la geometría del diseño: carriles en
 * x = 58, 173, 288, 403 y 518 sobre un ancho de 610; primer paso en y = 70 y
 * 44 px entre pasos.
 *
 * Carriles: Cliente · Workflow · clasificador (Jev u OpenAI) · LLM · Tools.
 *  - `inbound`: Cliente → Workflow.
 *  - `perception` y `verify`: Workflow → clasificador y de vuelta.
 *  - `llm`: Workflow → LLM ("ronda N") y LLM → Workflow (lo que pidió).
 *  - `tool`: Workflow → Tools y de vuelta: las tools las ejecuta el WORKFLOW
 *    (`execute_tool` de exoclaw) después de que el LLM las pide.
 *  - `plan`, `guard`, `cut`, `restart`: una caja dentro del Workflow.
 *  - `outbound`: Workflow → Cliente.
 */

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
  escalation: "escalación",
  send_reply: "send_reply",
  tag_closure: "cierre de tag",
  checkpoint_a: "corrientazo (A)",
  checkpoint_b: "corrientazo (B)",
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
      const label = n > 1 ? `ráfaga: ${n} mensajes` : "1 mensaje";
      return [{ ...base, from: CLIENT, to: WORKFLOW, status: "info", short: label, title: n > 1 ? `Ráfaga · ${n} mensajes` : "Mensaje del cliente", kind: "Entrada", t: time }];
    }
    case "perception":
    case "verify": {
      const verify = step.kind === "verify";
      const kind = verify ? "Clasificador · verificación" : "Clasificador · percepción";
      const n = count(step.answers);
      return [
        { ...base, dur: duration(step), from: WORKFLOW, to: CLASSIFIER, status: "classifier", short: verify ? "¿cubre cada asunto?" : `percepción · ${n} respuestas`, title: verify ? "Verificación" : "Percepción", kind, t: time },
        { ...base, from: CLASSIFIER, to: WORKFLOW, status: "classifier", short: verify ? `decisión: ${String(step.decision ?? "—")}` : "respuestas", title: verify ? "Decisión" : "Respuestas", kind, t: back },
      ];
    }
    case "plan":
      return [{ ...base, from: WORKFLOW, to: WORKFLOW, status: "neutral", short: `plan: ${count(step.checklist)} asuntos`, title: `Plan: ${count(step.checklist)} asuntos`, kind: "Plan · código", t: time }];
    case "llm": {
      const round = typeof step.round === "number" ? step.round : 1;
      const tools = names(step.tool_calls);
      const withText = textDiscarded(step) || step.text_fate === "pre_tool_message";
      const asked = tools.length
        ? `pide ${tools[0]}${tools.length > 1 ? ` +${tools.length - 1}` : ""}${withText ? " + texto" : ""}`
        : "texto";
      return [
        { ...base, dur: duration(step), from: WORKFLOW, to: LLM, status: "info", short: `ronda ${round}`, title: `Ronda ${round}`, kind: "LLM", t: time },
        { ...base, from: LLM, to: WORKFLOW, status: textDiscarded(step) ? "warn" : "info", short: asked, title: tools.length ? `Pide ${tools.join(", ")}` : "Texto final", kind: "LLM", t: back },
      ];
    }
    case "tool": {
      const name = typeof step.name === "string" ? step.name : "tool";
      const failed = step.ok === false;
      const error = typeof step.error === "string" ? step.error : "error";
      return [
        { ...base, dur: duration(step), from: WORKFLOW, to: TOOLS, status: "tool", short: name, title: name, kind: "Tool", t: time },
        { ...base, from: TOOLS, to: WORKFLOW, status: failed ? "bad" : "tool", short: failed ? `rechazada: ${error}` : "resultado", title: failed ? `Rechazada: ${error}` : "Resultado", kind: "Tool", t: back },
      ];
    }
    case "guard": {
      const name = typeof step.name === "string" ? step.name : "guarda";
      const removed = typeof step.before === "string" && step.before !== "" && step.after === "";
      return [{ ...base, from: WORKFLOW, to: WORKFLOW, status: removed ? "bad" : "warn", short: name, title: name, kind: "Guarda", t: time }];
    }
    case "cut": {
      const reason = typeof step.reason === "string" ? step.reason : "";
      const label = CUT_LABELS[reason] ?? reason ?? "corte";
      return [{ ...base, from: WORKFLOW, to: WORKFLOW, status: "neutral", short: label, title: `Corte: ${label}`, kind: "Corte del turno", t: time }];
    }
    case "restart": {
      const attempt = typeof step.attempt === "number" ? step.attempt : 1;
      const drained = typeof step.drained === "number" ? step.drained : 0;
      const label = `reinicio ${attempt} · +${drained} mensaje${drained === 1 ? "" : "s"}`;
      return [{ ...base, from: WORKFLOW, to: WORKFLOW, status: "warn", short: label, title: `Reinicio ${attempt}`, kind: "Reinicio", t: time }];
    }
    case "outbound": {
      const n = count(step.bubbles);
      return [{ ...base, from: WORKFLOW, to: CLIENT, status: outboundStatus(step), short: `${n} ${n === 1 ? "envío" : "envíos"}`, title: `Envío · ${n}`, kind: "Envío", t: time }];
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
    lanes: ["Cliente", "Workflow", opts.classifierLabel ?? "Clasificador", "LLM", "Tools"],
    lanesX: LANE_X,
    rows,
    width: SEQ_WIDTH,
    height: SEQ_Y0 + Math.max(rows.length - 1, 0) * SEQ_DY + 34,
    usesClassifier: rows.some((r) => r.from === CLASSIFIER || r.to === CLASSIFIER),
  };
}
