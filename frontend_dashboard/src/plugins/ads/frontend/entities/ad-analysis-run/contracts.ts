/**
 * Schemas Zod para validar las respuestas del buzón de análisis en el boundary
 * HTTP. Se activan en `api.ts` con `.parse(raw)` / `.safeParse(raw)`, así un
 * cambio de contrato del backend (`src/plugins/ads/api/analysis.py`)
 * truena temprano y con mensaje claro en vez de propagar `undefined`.
 *
 * El backend emite snake_case (`run_id`, `execution_id`, `example_input`); los
 * schemas hacen el `.transform()` a la forma camelCase de `model.ts` para que el
 * resto del plugin no toque snake_case. `payload`/`input`/`result`/`awaiting`
 * son `z.unknown()` — el contrato del agente es libre y se renderiza como JSON.
 */

import { z } from "zod";

export const runStatusSchema = z.enum([
  "pending",
  "running",
  "awaiting_approval",
  "completed",
  "failed",
]);

/** Una opción del selector — `GET /api/ads/analysis/agents`. */
export const agentOptionSchema = z
  .object({
    id: z.string(),
    label: z.string(),
    // Qué análisis hace el agente (el selector la muestra bajo el combo).
    // `.default(null)` tolera catálogos legacy sin descripción.
    description: z.string().nullable().default(null),
    example_input: z.unknown(),
  })
  .transform((a) => ({
    id: a.id,
    label: a.label,
    description: a.description,
    exampleInput: a.example_input,
  }));

export const agentsListResponseSchema = z.array(agentOptionSchema);

export const runEventSchema = z
  .object({
    event_id: z.string(),
    type: z.string(),
    payload: z.unknown().optional(),
  })
  .transform((e) => ({
    eventId: e.event_id,
    type: e.type,
    payload: e.payload,
  }));

/** El record completo — `GET /api/ads/analysis/runs/{run_id}` y el `snapshot` del SSE. */
export const runRecordSchema = z
  .object({
    run_id: z.string(),
    agent: z.string(),
    input: z.unknown(),
    status: runStatusSchema,
    events: z.array(runEventSchema).default([]),
    result: z.unknown().optional(),
    awaiting: z.unknown().optional(),
    error: z.unknown().optional(),
    execution_id: z.string().nullable().optional(),
    // Historial versionado (2026-07-09): cada análisis queda fechado.
    // `.default(null)` tolera records legacy pre-timestamp.
    created_at_ms: z.number().int().nullable().default(null),
    // La campaña ACTIVA al disparar — el historial del inspector filtra por
    // ella. `.default(null)` tolera records legacy pre-campaña.
    campaign_id: z.string().nullable().default(null),
  })
  .transform((r) => ({
    runId: r.run_id,
    agent: r.agent,
    input: r.input,
    status: r.status,
    events: r.events,
    result: r.result,
    awaiting: r.awaiting,
    error: r.error,
    executionId: r.execution_id ?? undefined,
    createdAtMs: r.created_at_ms,
    campaignId: r.campaign_id,
  }));

/** El historial — `GET /api/ads/analysis/runs` (sin `events`, más nuevo primero). */
export const runsListResponseSchema = z.object({
  runs: z.array(runRecordSchema),
});

/** Respuesta del disparo — `POST /api/ads/analysis/runs` → `{ run_id }`. */
export const triggerRunResponseSchema = z.object({
  run_id: z.string(),
});

/** Respuesta del resume del HITL — `POST .../approve` → `{ status: "resumed" }`. */
export const approveResponseSchema = z.object({
  status: z.string(),
});
