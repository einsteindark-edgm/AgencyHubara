/**
 * Contratos Zod de la entidad `mba-rollout` (D2.3): estado del rollout en
 * Meta (settings + allowlist + readiness) y el outcome de cada escritura.
 * Espejan `use_cases/rollout_control.py`.
 */
import { z } from "zod";

export const mbaAudienceSchema = z.enum(["ALLOWLISTED_ONLY", "EVERYONE"]);

export const mbaRolloutCheckSchema = z.object({
  code: z.string(),
  ok: z.boolean(),
  detail: z.string(),
});

export const mbaRolloutEntrySchema = z.object({
  id: z.string(),
  phone: z.string(),
  in_hubara: z.boolean(),
});

export const mbaRolloutHistorySchema = z.object({
  at_ms: z.number(),
  action: z.string(),
  value: z.string(),
  ok: z.boolean(),
});

export const mbaRolloutStatusSchema = z.object({
  agent_id: z.string(),
  entity_id: z.string().nullable(),
  rollout_enabled: z.boolean().nullable(),
  ai_audience: z.string().nullable(),
  allowlist: z.array(mbaRolloutEntrySchema),
  checks: z.array(mbaRolloutCheckSchema),
  can_enable: z.boolean(),
  everyone_allowed: z.boolean(),
  last_sync: z.object({ status: z.string(), at_ms: z.number() }).passthrough().nullable(),
  history: z.array(mbaRolloutHistorySchema),
});

export const mbaRolloutOutcomeSchema = z.object({
  agent_id: z.string(),
  applied: z.boolean(),
  reason: z.string(),
  blocked: z.array(z.string()),
  error: z.object({ kind: z.string(), detail: z.string(), status: z.number().nullable() }).nullable(),
  checks: z.array(mbaRolloutCheckSchema),
});
