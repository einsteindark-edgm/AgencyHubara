/**
 * Plugin `agents_admin` — barrel del frontend.
 *
 * Exporta el `default` (AgentsSection) que el shell consume via
 * `lazy(() => import("@plugins/agents_admin/frontend"))` desde el registry.
 * Plus named re-exports de las features para tests y back-compat.
 *
 * Plugin frontend-only (sin agente Temporal, sin API propio).
 */
export { default, AgentsSection } from "./AgentsSection";

export { AgentsList } from "@plugins/agents_admin/frontend/features/agents-list";
export { AgentsPrompts } from "@plugins/agents_admin/frontend/features/agents-prompts";
export { AgentsInspector } from "@plugins/agents_admin/frontend/features/agents-inspector";
