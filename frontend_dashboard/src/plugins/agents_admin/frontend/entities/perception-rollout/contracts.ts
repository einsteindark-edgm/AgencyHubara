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

/** Motor de decisiones (F7): el interruptor de una capacidad. */
export const capabilityControlSchema = z.object({
  mode: perceptionModeSchema,
  ceiling: perceptionModeSchema,
  readiness: z.record(z.string(), z.array(rolloutCheckSchema)).catch({}).default({}),
  can: z.record(z.string(), z.array(z.string())).catch({}).default({}),
});

export const workflowModeSchema = z.enum(["off", "canary", "on"]).catch("off");

/** Motor de decisiones (F7): la versión del workflow de ventas (V2 por canary). */
export const workflowControlSchema = z.object({
  mode: workflowModeSchema,
  ceiling: perceptionModeSchema,
  readiness: z.record(z.string(), z.array(rolloutCheckSchema)).catch({}).default({}),
  can: z.record(z.string(), z.array(z.string())).catch({}).default({}),
});

const WORKFLOW_OFF = { mode: "off" as const, ceiling: "off" as const, readiness: {}, can: {} };

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
  // Motor de decisiones (F7). Una API vieja no los trae: todo en reglas y V1.
  capabilities: z.record(z.string(), capabilityControlSchema).catch({}).default({}),
  workflow_v2: workflowControlSchema.catch(WORKFLOW_OFF).default(WORKFLOW_OFF),
  // ¿Los números de prueba deciden con Jev? (2026-10-06; una API vieja no lo trae: no).
  test_numbers_jev: z.boolean().catch(false).default(false),
});

// ── El motor de decisiones: su versión y lo que decide (2026-10-02) ─────────

/** Una parte del software donde el motor decide algo (en el orden de la conversación). */
export const enginePlaceSchema = z.object({
  id: z.string(),
  label: z.string().catch("").default(""),
});

/** Una decisión del motor: dónde actúa, qué resuelve y quién la decide hoy. */
export const engineDecisionAboutSchema = z.object({
  capability: z.string(),
  name: z.string().catch("").default(""),
  where: z.array(z.string()).catch([]).default([]),
  solves: z.string().catch("").default(""),
  /** La decisión de la que es variante (comparte su interruptor), o null. */
  variant_of: z.string().nullable().catch(null).default(null),
  mode: perceptionModeSchema,
  /** El paquete del que sale (`id@versión`): el de la tienda o el de la App Operador. */
  bundle: z.string().catch("").default(""),
});

/** Un paquete de decisión que corre en el motor (la tienda, la App Operador). */
export const engineBundleSchema = z.object({
  id: z.string().catch("").default(""),
  version: z.number().nullable().catch(null).default(null),
  ref: z.string(),
  oracle: z.string().catch("").default(""),
  /** Para qué es, en palabras del operador. */
  name: z.string().catch("").default(""),
});

function tolerantList<T extends z.ZodTypeAny>(item: T) {
  return z
    .array(z.unknown())
    .catch([])
    .default([])
    .transform((items) =>
      items.flatMap((raw) => {
        const parsed = item.safeParse(raw);
        return parsed.success ? [parsed.data as z.output<T>] : [];
      }),
    );
}

export const decisionEngineSchema = z.object({
  bundle: z.object({
    id: z.string().catch("").default(""),
    version: z.number().nullable().catch(null).default(null),
    ref: z.string().catch("").default(""),
    oracle: z.string().catch("").default(""),
    engine_contract: z.number().nullable().catch(null).default(null),
    /** El paquete que trae el código (lo que corre una tienda sin configurar). */
    code_default: z.string().catch("").default(""),
  }),
  /** Todos los paquetes que corren en el motor (2026-10-06); vacío con un backend anterior. */
  bundles: tolerantList(engineBundleSchema),
  profile: z.string().catch("").default(""),
  places: tolerantList(enginePlaceSchema),
  decisions: tolerantList(engineDecisionAboutSchema),
  turn: z
    .object({ policy: z.string().catch("").default(""), topics: z.number().catch(0).default(0), questions: z.number().catch(0).default(0) })
    .nullable()
    .catch(null)
    .default(null),
});
