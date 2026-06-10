/**
 * Schemas Zod para validar respuestas del backend de `catalog` (sync).
 *
 * Backend: plugin `catalog.api` (FastAPI) sirve `/api/catalog/*` — dispara y
 * observa el `CatalogSyncWorkflow` de Temporal (Medusa → copia local → Meta).
 * Cada shape acá espeja exactamente el dict que devuelve el endpoint
 * correspondiente en `hubara_agency/src/plugins/catalog/api/__init__.py`.
 */

import { z } from "zod";

/* ── Enums ─────────────────────────────────────────────────────────────── */

/** Estado de un paso individual del workflow. */
export const syncStepStatusSchema = z.enum([
  "pending",
  "running",
  "done",
  "skipped",
  "failed",
]);

/** Estado global de un run (mapeo del WorkflowExecutionStatus de Temporal). */
export const syncStatusSchema = z.enum([
  "running",
  "completed",
  "failed",
  "cancelled",
  "unknown",
]);

/* ── Paso del step-by-step ─────────────────────────────────────────────── */

export const syncStepSchema = z.object({
  key: z.string(),
  label: z.string(),
  status: syncStepStatusSchema,
  detail: z.string(),
});

/* ── GET /api/catalog/sync/{id} — estado + pasos en vivo ───────────────── */

export const syncDetailSchema = z.object({
  workflow_id: z.string(),
  status: syncStatusSchema,
  started_at_ms: z.number().nullable(),
  finished_at_ms: z.number().nullable(),
  product_count: z.number().int(),
  version: z.string(),
  error: z.string().nullable(),
  steps: z.array(syncStepSchema),
});

/* ── POST /api/catalog/sync — disparo ──────────────────────────────────── */

export const triggerSyncResponseSchema = z.object({
  workflow_id: z.string(),
  run_id: z.string(),
  started_at_ms: z.number(),
  status: syncStatusSchema,
  already_running: z.boolean(),
});

/* ── GET /api/catalog/syncs — historial ────────────────────────────────── */

export const syncHistoryItemSchema = z.object({
  workflow_id: z.string(),
  run_id: z.string(),
  status: syncStatusSchema,
  started_at_ms: z.number().nullable(),
  finished_at_ms: z.number().nullable(),
  triggered_by: z.string(),
});

export const syncHistoryResponseSchema = z.object({
  syncs: z.array(syncHistoryItemSchema),
  // `available=false` → la visibility de Temporal no respondió; la UI muestra
  // estado vacío explícito en vez de error (mismo patrón que orders).
  available: z.boolean(),
  error_detail: z.string().nullable(),
});

/* ── GET /api/catalog/snapshot — estado de la copia local ──────────────── */

export const snapshotInfoSchema = z.object({
  exists: z.boolean(),
  version: z.string().nullable(),
  product_count: z.number().int(),
  fetched_at: z.string().nullable(),
  age_minutes: z.number().int().nullable(),
  stale: z.boolean(),
  max_age_minutes: z.number().int(),
  snapshot_dir: z.string(),
});

/* ── Tipos inferidos ───────────────────────────────────────────────────── */

export type SyncStepStatus = z.infer<typeof syncStepStatusSchema>;
export type SyncStatus = z.infer<typeof syncStatusSchema>;
export type SyncStep = z.infer<typeof syncStepSchema>;
export type SyncDetail = z.infer<typeof syncDetailSchema>;
export type TriggerSyncResponse = z.infer<typeof triggerSyncResponseSchema>;
export type SyncHistoryItem = z.infer<typeof syncHistoryItemSchema>;
export type SyncHistoryResponse = z.infer<typeof syncHistoryResponseSchema>;
export type SnapshotInfo = z.infer<typeof snapshotInfoSchema>;
