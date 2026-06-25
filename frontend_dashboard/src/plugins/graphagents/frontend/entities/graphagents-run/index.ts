/**
 * Barrel del dominio "graphagents-run" — la superficie pública de la entity.
 * Las features y el page importan SOLO de acá (`@plugins/graphagents/frontend/
 * entities/graphagents-run`), nunca de un subpath profundo.
 */
export type {
  AgentOption,
  RunEvent,
  RunRecord,
  RunStatus,
} from "./model";
export { isAwaitingApproval, isTerminalStatus } from "./model";

export { graphagentsRunKeys } from "./keys";

export {
  useAgents,
  useRun,
  useTriggerRun,
  useApproveRun,
  useGraphAgentsRunEvents,
} from "./api";

export {
  agentOptionSchema,
  agentsListResponseSchema,
  approveResponseSchema,
  runEventSchema,
  runRecordSchema,
  runStatusSchema,
  triggerRunResponseSchema,
} from "./contracts";
