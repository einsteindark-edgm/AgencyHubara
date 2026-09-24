import { z } from "zod";

/**
 * Encendido del bot nuevo (capas con clasificador) — `/api/agents/perception/rollout`,
 * cast al contrato `perception-rollout@v1` de chats (plan del laboratorio PR 16).
 * Tolerante (L-10): lo que falte degrada a un neutro.
 */

export const perceptionModeSchema = z.enum(["off", "shadow", "canary", "on"]).catch("off");

export const rolloutCheckSchema = z.object({
  code: z.string(),
  ok: z.boolean().catch(false),
  detail: z.string().catch("").default(""),
});

export const rolloutSchema = z.object({
  state: z
    .object({
      mode: perceptionModeSchema,
      canary_percent: z.number().catch(0).default(0),
      test_numbers: z.array(z.string()).catch([]).default([]),
      updated_at_ms: z.number().nullable().catch(null).default(null),
      updated_by: z.string().nullable().catch(null).default(null),
    })
    .catch({ mode: "off", canary_percent: 0, test_numbers: [], updated_at_ms: null, updated_by: null }),
  ceiling: perceptionModeSchema,
  profile: z.string().catch("").default(""),
  metrics: z
    .object({
      days: z.number().catch(0).default(0),
      turns: z.number().catch(0).default(0),
      fallback_rate: z.number().nullable().catch(null).default(null),
      p95_ms: z.number().nullable().catch(null).default(null),
    })
    .catch({ days: 0, turns: 0, fallback_rate: null, p95_ms: null }),
  readiness: z.record(z.string(), z.array(rolloutCheckSchema)).catch({}).default({}),
  can: z.record(z.string(), z.array(z.string())).catch({}).default({}),
});
