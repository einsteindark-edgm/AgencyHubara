/**
 * Plugin `graphagents` — barrel del frontend.
 *
 * Default-exporta `GraphAgentsSection` (la Page) — el shell la consume vía lazy
 * import desde el registry generado (`assertPluginModule` lo verifica EN
 * COMPILACIÓN). Plus named re-exports de las features para tests.
 */
export { default, GraphAgentsSection } from "./GraphAgentsSection";

export { TriggerRun } from "@plugins/graphagents/frontend/features/trigger-run";
export { RunResult } from "@plugins/graphagents/frontend/features/run-result";
export { HitlDecision } from "@plugins/graphagents/frontend/features/hitl-decision";
