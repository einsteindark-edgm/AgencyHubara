import { ToolCall, TraceStep } from "./messages";

/**
 * La CASCADA de una ejecución (§F9/F10 — estilo test navigator de Xcode):
 * agentes en orden de ejecución, y dentro de cada agente sus tools en orden.
 * Lógica pura compartida por el árbol de Runs (extension host) y el panel
 * "Ejecución" (webview) — una sola convención de numeración (`n` / `n.i`),
 * la misma que los badges del canvas.
 */
export interface StepEntry {
  order: number;
  agent: string;
  status: string;
  ms?: number;
  retries?: number;
  tools: Array<{ order: string; tool: string }>;
}

export function toStepEntries(steps: TraceStep[], nodeTraces: Record<string, ToolCall[]>): StepEntry[] {
  const out: StepEntry[] = [];
  let n = 0;
  for (const step of steps) {
    if (!step.agent) {
      continue;
    }
    n++;
    const ledger = nodeTraces[step.agent];
    // el orden REAL si el ledger llegó (flow-trace); si no, el declarado del plan
    const tools = ledger ? ledger.map((c) => c.tool) : (step.tools ?? []);
    out.push({
      order: n,
      agent: step.agent,
      status: step.runtime?.status ?? "done",
      ms: step.runtime?.ms,
      retries: step.runtime?.retries,
      tools: tools.map((tool, i) => ({ order: `${n}.${i + 1}`, tool })),
    });
  }
  return out;
}
