/**
 * Entidad "ad-analysis-run": un run de análisis de Ads (CTWA) disparado desde
 * el dashboard. El backend (`/api/ads/analysis`) crea el record al disparar, lo
 * mueve por estados a medida que pollea Conductor de la caja de análisis, y
 * republica cada cambio al stream multiplexado del dashboard (dominio
 * `ads`). El frontend lee el record con `useRun(runId)` y se entera de
 * los cambios por `useAdAnalysisRunEvents()`.
 */

/** Estados lógicos del run (espejo de `conductor.interpret` en el backend). */
export type RunStatus =
  | "pending"
  | "running"
  | "awaiting_approval"
  | "completed"
  | "failed";

/** Una opción del selector de agentes — `GET /api/ads/analysis/agents`. */
export interface AgentOption {
  id: string;
  label: string;
  /** Qué análisis hace este agente — se muestra bajo el selector. */
  description: string | null;
  /** JSON de ejemplo (fallback del textarea cuando Meta no está conectado). */
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
 * El record completo del run — `GET /api/ads/analysis/runs/{run_id}`.
 *
 * `result` está poblado cuando `status === "completed"`; `awaiting` cuando
 * `status === "awaiting_approval"` (el contexto que el HITL debe revisar antes
 * de aprobar/rechazar). `execution_id` es el id en AgentSpan/Conductor.
 */
export interface RunRecord {
  runId: string;
  agent: string;
  input: unknown;
  /** Fecha del análisis (historial versionado) — null en records legacy. */
  createdAtMs: number | null;
  /** Campaña activa al disparar (el historial es por campaña) — null legacy. */
  campaignId: string | null;
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
