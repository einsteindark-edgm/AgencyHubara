/**
 * Entidad "graphagents-run": un run de un agente de GraphAgents disparado desde
 * el dashboard. El backend (`/api/graphagents`) crea el record al disparar, lo
 * mueve por estados a medida que pollea Conductor de la caja GraphAgents, y
 * republica cada cambio al stream multiplexado del dashboard (dominio
 * `graphagents`). El frontend lee el record con `useRun(runId)` y se entera de
 * los cambios por `useGraphAgentsRunEvents()`.
 */

/** Estados lógicos del run (espejo de `conductor.interpret` en el backend). */
export type RunStatus =
  | "pending"
  | "running"
  | "awaiting_approval"
  | "completed"
  | "failed";

/** Una opción del selector de agentes — `GET /api/graphagents/agents`. */
export interface AgentOption {
  id: string;
  label: string;
  /** JSON de ejemplo que se inyecta en el textarea al elegir el agente. */
  exampleInput: unknown;
}

/**
 * Un evento del timeline del run (lo appendea el orchestrator del backend).
 * `type` ∈ run.started | run.awaiting_approval | run.result | run.failed.
 * `payload` es opaco (forma distinta por tipo: execution_id / context / output /
 * error) — el render lo trata como JSON.
 */
export interface RunEvent {
  eventId: string;
  type: string;
  payload?: unknown;
}

/**
 * El record completo del run — `GET /api/graphagents/runs/{run_id}`.
 *
 * `result` está poblado cuando `status === "completed"`; `awaiting` cuando
 * `status === "awaiting_approval"` (el contexto que el HITL debe revisar antes
 * de aprobar/rechazar). `execution_id` es el id en AgentSpan/Conductor.
 */
export interface RunRecord {
  runId: string;
  agent: string;
  input: unknown;
  status: RunStatus;
  events: RunEvent[];
  result?: unknown;
  awaiting?: unknown;
  error?: unknown;
  executionId?: string;
}

/** Estados terminales — el stream ya no va a empujar más cambios. */
export function isTerminalStatus(status: RunStatus): boolean {
  return status === "completed" || status === "failed";
}

/** True si el run está esperando una decisión humana (muestra el panel HITL). */
export function isAwaitingApproval(record: RunRecord | null | undefined): boolean {
  return record?.status === "awaiting_approval";
}
