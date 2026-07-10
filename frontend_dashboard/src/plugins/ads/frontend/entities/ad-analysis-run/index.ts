/**
 * Barrel del dominio "ad-analysis-run" — la superficie pública de la entity.
 * Las features y el page importan SOLO de acá (`@plugins/ads/frontend/
 * entities/ad-analysis-run`), nunca de un subpath profundo.
 */
export type {
  AgentOption,
  AnalysisReport,
  RunEvent,
  RunRecord,
  RunStatus,
} from "./model";
export { analysisReport, isAwaitingApproval, isTerminalStatus } from "./model";

export { adAnalysisRunKeys } from "./keys";

export {
  useAgents,
  useRun,
  useRuns,
  useTriggerRun,
  useApproveRun,
  useAdAnalysisRunEvents,
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
