// Zod schemas — espejo de hubara_agency/src/plugins/system_map/domain/contracts.py.
// Validación al boundary (apiClient.get<unknown>() + schema.parse(...)).
import { z } from "zod";

export const NodeKindSchema = z.enum([
  "plugin",
  "frontend_unit",  // sidebar + section combinados en una sola caja
  "api_router",
  "api_endpoint",
  "worker",
  "task_queue",
]);
export type NodeKind = z.infer<typeof NodeKindSchema>;

export const EdgeKindSchema = z.enum([
  "depends_on",     // plugin → plugin (declarado en manifest)
  "belongs_to",     // plugin → child (pertenencia muda, visualmente gris punteado)
  "consumes_queue", // worker → task_queue
  "uses_api",       // frontend_unit → api_router (lazy)
  "invokes_worker", // api_router → worker (REAL del código Python)
  "mounts_on",      // V2
]);
export type EdgeKind = z.infer<typeof EdgeKindSchema>;

export const CompletenessStatusSchema = z.enum([
  "complete",
  "frontend_only",
  "api_only",
  "agent_only",
  "frontend_api",
  "frontend_agent",
  "api_agent",
  "empty",
]);
export type CompletenessStatus = z.infer<typeof CompletenessStatusSchema>;

export const OrphanReasonSchema = z
  .enum([
    "empty_plugin",
    "frontend_incomplete",
    "worker_no_task_queue",
    "api_router_no_prefix",
    "manifest_invalid",
    "depends_on_missing",
  ])
  .nullable();
export type OrphanReason = z.infer<typeof OrphanReasonSchema>;

export const NodeSchema = z.object({
  id: z.string(),
  kind: NodeKindSchema,
  plugin_id: z.string(),
  label: z.string(),
  data: z.record(z.string(), z.unknown()),
  is_orphan: z.boolean(),
  orphan_reason: OrphanReasonSchema,
});
export type SystemNode = z.infer<typeof NodeSchema>;

export const EdgeSchema = z.object({
  id: z.string(),
  source: z.string(),
  target: z.string(),
  kind: EdgeKindSchema,
  label: z.string().nullable(),
});
export type SystemEdge = z.infer<typeof EdgeSchema>;

export const PluginSummarySchema = z.object({
  id: z.string(),
  display_name: z.string(),
  version: z.string(),
  description: z.string().nullable(),
  has_frontend: z.boolean(),
  has_api: z.boolean(),
  has_agent: z.boolean(),
  completeness: CompletenessStatusSchema,
  node_count: z.number().int().nonnegative(),
  orphan_count: z.number().int().nonnegative(),
});
export type PluginSummary = z.infer<typeof PluginSummarySchema>;

export const StatsSchema = z.object({
  total_nodes: z.number().int().nonnegative(),
  total_edges: z.number().int().nonnegative(),
  total_plugins: z.number().int().nonnegative(),
  orphan_count: z.number().int().nonnegative(),
  by_kind: z.record(z.string(), z.number().int().nonnegative()),
});
export type Stats = z.infer<typeof StatsSchema>;

export const SystemGraphSchema = z.object({
  version: z.string(),
  generated_at: z.string(),
  nodes: z.array(NodeSchema),
  edges: z.array(EdgeSchema),
  plugins: z.array(PluginSummarySchema),
  stats: StatsSchema,
  warnings: z.array(z.string()),
});
export type SystemGraph = z.infer<typeof SystemGraphSchema>;
