/**
 * Contratos Zod de la entidad `mba-sync` (D2.2): el estado del último sync
 * con Meta (vault), el plan de solo lectura (diff puro) y el resultado de un
 * apply. Espejan `domain/sync.py` + `use_cases/sync_agent.py` del backend.
 */
import { z } from "zod";

export const mbaSyncActionSchema = z.enum(["create", "update", "replace", "delete", "noop", "skip"]);

export const mbaSyncOpSchema = z.object({
  section: z.string(),
  label: z.string(),
  action: mbaSyncActionSchema,
  body: z.record(z.string(), z.unknown()),
  remote_id: z.string().nullable(),
  reason: z.string(),
  connector_label: z.string().nullable(),
  connector_remote_id: z.string().nullable(),
});

export const mbaSyncPlanSchema = z.object({
  agent_id: z.string(),
  entity_id: z.string().nullable(),
  fingerprint: z.string(),
  blocked: z.array(z.string()),
  counts: z.record(z.string(), z.number()),
  ops: z.array(mbaSyncOpSchema),
});

export const mbaSyncErrorSchema = z.object({
  kind: z.string(),
  detail: z.string(),
  status: z.number().nullable(),
});

export const mbaSyncResultSchema = z.object({
  section: z.string(),
  label: z.string(),
  action: z.string(),
  ok: z.boolean(),
  remote_id: z.string().nullable(),
  error: mbaSyncErrorSchema.nullable(),
  skipped: z.string().nullable(),
});

export const mbaSyncLastApplySchema = z.object({
  at_ms: z.number(),
  status: z.string(),
  fingerprint: z.string(),
  counts: z.object({
    changes: z.number(),
    ok: z.number(),
    failed: z.number(),
    skipped: z.number(),
  }),
  results: z.array(mbaSyncResultSchema),
});

export const mbaSyncLastAttemptSchema = z.object({
  at_ms: z.number(),
  reason: z.string(),
  blocked: z.array(z.string()).optional(),
  fingerprint: z.string().optional(),
});

export const mbaSyncStateSchema = z.object({
  agent_id: z.string(),
  state: z
    .object({
      entity_id: z.string().optional(),
      last_apply: mbaSyncLastApplySchema.optional(),
      last_attempt: mbaSyncLastAttemptSchema.optional(),
    })
    .nullable(),
});

export const mbaSyncOutcomeSchema = z.object({
  agent_id: z.string(),
  applied: z.boolean(),
  reason: z.string(),
  status: z.string(),
  results: z.array(mbaSyncResultSchema),
  plan: z.object({ fingerprint: z.string() }).passthrough().nullable(),
  blocked: z.array(z.string()),
  error: mbaSyncErrorSchema.nullable(),
});
