import { z } from "zod";

/**
 * Contratos de Calidad LLM con la vista del laboratorio sobre producción —
 * `/api/agents/evals/production/*` (cast al contrato `evals@v1` de chats,
 * 2026-10-02). Las formas son las del laboratorio sin brazos ni repeticiones.
 *
 * Tolerantes por diseño (L-10): campos ausentes toman un neutro, los
 * enumerables que el backend puede extender degradan, y una lista con un
 * elemento raro descarta ESE elemento en vez de vaciar la vista.
 */

const nullableNumber = z.number().nullable().catch(null).default(null);
const nullableString = z.string().nullable().catch(null).default(null);

function tolerantArray<T extends z.ZodTypeAny>(item: T) {
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

export const verdictSchema = z.enum(["FALLA", "ALERTA", "PASA", "SIN_DATOS"]).catch("SIN_DATOS");
export const checkVerdictSchema = z.enum(["pasa", "falla", "no_aplica", "desconocido", "sin_senal"]).catch("desconocido");
export const levelSchema = z.enum(["critico", "mayor", "menor"]).catch("menor");
/** Qué bot respondió el episodio: `nuevo` = el workflow nuevo (el bot Jev). */
const BOTS = ["actual", "nuevo", "mixto"] as const;
type EpisodeBot = (typeof BOTS)[number];

/** El bot de cada episodio; un valor que no se conoce se descarta (no se adivina el bot). */
const botsSchema = z
  .record(z.string(), z.unknown())
  .catch({})
  .default({})
  .transform((raw) =>
    Object.fromEntries(Object.entries(raw).filter((e): e is [string, EpisodeBot] => (BOTS as readonly unknown[]).includes(e[1]))),
  );

// ── La lista ────────────────────────────────────────────────────────────────

export const conversationRowSchema = z.object({
  session_id: z.string(),
  turns: z.number().catch(0).default(0),
  episodes: z.array(z.string()).catch([]).default([]),
  last_at_ms: nullableNumber,
  verdicts: z.record(z.string(), verdictSchema).catch({}).default({}),
  bots: botsSchema,
});

export const conversationsSchema = z.object({
  days: z.number().catch(56).default(56),
  /** Bot por el que filtró el servidor; null = todos o una API sin el filtro. */
  bot: z.enum(["actual", "nuevo"]).nullable().catch(null).default(null),
  conversations: tolerantArray(conversationRowSchema),
});

// ── El hilo ─────────────────────────────────────────────────────────────────

export const threadMessageSchema = z.object({
  role: z.string().default("user"),
  content: z.string().catch("").default(""),
  timestamp: nullableString,
  sender: nullableString,
  kind: nullableString,
  wamid: nullableString,
  has_image: z.boolean().catch(false).default(false),
});

export const burstMessageSchema = z.object({
  text: z.string().catch("").default(""),
  ts_ms: nullableNumber,
  wamid: nullableString,
});

export const threadTurnSchema = z.object({
  turn_key: z.string(),
  episode_id: z.string().catch("").default(""),
  turn: z.number().catch(0).default(0),
  at_ms: nullableNumber,
  burst: tolerantArray(burstMessageSchema),
});

export const threadSchema = z.object({
  session_id: z.string(),
  messages: tolerantArray(threadMessageSchema),
  turns: tolerantArray(threadTurnSchema),
});

// ── La ventana del turno ────────────────────────────────────────────────────

export const traceStepSchema = z
  .object({
    i: z.number().optional(),
    at_ms: z.number().nullable().catch(null).default(null),
    kind: z.string().catch("desconocido"),
    dur_ms: z.number().nullable().optional().catch(null),
  })
  .passthrough();

export const turnTraceSchema = z.object({
  fidelity: z.enum(["v1", "v2"]).catch("v1"),
  trace: z.record(z.string(), z.unknown()).catch({}).default({}),
  steps: z.array(traceStepSchema).catch([]).default([]),
});

/** Una decisión de Jev en el turno (`trace.decisions`, del registro de la conversación). */
export const engineDecisionSchema = z.object({
  stage: z.string().catch("turno").default("turno"),
  message: z.number().optional().catch(undefined),
  capability: z.string(),
  by: z.string().catch("").default(""),
  provider: z.string().catch("").default(""),
  value: z.unknown().optional(),
  rule: z.unknown().optional(),
  jev: z.unknown().optional(),
  reason: z.string().optional().catch(undefined),
  answers: tolerantArray(
    z.object({
      q: z.string().optional().catch(undefined),
      p: z.number().optional().catch(undefined),
      choice: z.string().optional().catch(undefined),
      confidence: z.number().optional().catch(undefined),
    }),
  ),
});

export const engineDecisionsSchema = tolerantArray(engineDecisionSchema);

// ── Las evaluaciones turno por turno ────────────────────────────────────────

export const topicCoverageSchema = z.object({
  topic: z.string(),
  turn: nullableNumber,
  msg: nullableNumber,
  covered: z.boolean().catch(false).default(false),
  evidence: z.string().catch("").default(""),
});

export const evalResultSchema = z.object({
  check_id: z.string(),
  verdict: checkVerdictSchema,
  level: levelSchema.optional().catch(undefined),
  turn: nullableNumber,
  evidence: nullableString,
  critique: nullableString,
  source: nullableString,
  topics: tolerantArray(topicCoverageSchema),
});

export const episodeEvaluationSchema = z.object({
  episode_id: z.string().catch("").default(""),
  verdict: verdictSchema,
  results: tolerantArray(evalResultSchema),
});

export const evaluationsSchema = z.object({
  episodes: tolerantArray(episodeEvaluationSchema),
});

// ── El informe de Jev ───────────────────────────────────────────────────────

const rate = z.number().nullable().catch(null).default(null);
const count = z.number().catch(0).default(0);

export const jevReportSchema = z.object({
  days: z.number().catch(56).default(56),
  episodes: count,
  turns: count,
  errors: count,
  perception: z
    .object({ turns: count, fallbacks: count, fallback_rate: rate, p50_ms: rate, p95_ms: rate })
    .nullable()
    .catch(null)
    .default(null),
  verify: z
    .object({ turns: count, decisions: z.record(z.string(), z.number()).catch({}).default({}), p95_ms: rate })
    .nullable()
    .catch(null)
    .default(null),
  complement_rate: rate,
  extra_round_rate: rate,
  decisions: z
    .object({
      total: count,
      by: z.record(z.string(), z.number()).catch({}).default({}),
      asked_jev: count,
      jev_failed: count,
      jev_failed_rate: rate,
      jev_failed_by_reason: z.record(z.string(), z.number()).catch({}).default({}),
      jev_failed_by_capability: z.record(z.string(), z.number()).catch({}).default({}),
    })
    .nullable()
    .catch(null)
    .default(null),
  cost_per_turn_usd: rate,
  perception_cost_per_turn_usd: rate,
});
