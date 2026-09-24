import { z } from "zod";

/**
 * Contratos del laboratorio de conversaciones — `/api/lab/*` (cast al
 * contrato `lab@v1` de chats; plan §3.7 y §11).
 *
 * Tolerantes por diseño (L-10): campos ausentes toman un neutro y los
 * enumerables que el backend puede extender (veredictos, fases) degradan en
 * vez de vaciar la vista. Los pasos de la traza viajan con sus campos propios
 * (`passthrough`): el detalle los lee por tipo.
 */

const nullableNumber = z.number().nullable().catch(null).default(null);
const nullableString = z.string().nullable().catch(null).default(null);

/**
 * Lista tolerante POR ELEMENTO (L-10): un elemento con otra forma se descarta
 * en vez de vaciar la lista entera (un `.catch([])` a nivel lista dejaba la
 * sección en blanco por un solo elemento raro).
 */
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

/** Veredicto del episodio (mismo enum que Calidad LLM). */
export const episodeVerdictSchema = z.enum(["FALLA", "ALERTA", "PASA", "SIN_DATOS"]).catch("SIN_DATOS");

/** Resultado de un check sobre el episodio. */
export const checkVerdictSchema = z.enum(["pasa", "falla", "no_aplica", "desconocido"]).catch("desconocido");

// ── Corridas ────────────────────────────────────────────────────────────────

export const runSchema = z.object({
  run_id: z.string(),
  bench_id: nullableString,
  arms: z.array(z.string()).catch([]).default([]),
  reps: nullableNumber,
  registry_version: nullableNumber,
  counts: z.record(z.string(), z.number()).catch({}).default({}),
  phase: nullableString,
  turns_done: nullableNumber,
  turns_total: nullableNumber,
  spent_usd: nullableNumber,
  error: nullableString,
  notes: z.array(z.string()).catch([]).default([]),
  started_at_ms: nullableNumber,
  updated_at_ms: nullableNumber,
  /** Sin fase terminal y sin reportes de la caja hace más de 30 min (ausente = no). */
  stale: z.boolean().optional().catch(undefined),
});

export const runsSchema = z.object({ runs: tolerantArray(runSchema) });

export const estimateArmSchema = z.object({
  id: z.string(),
  label: z.string().default(""),
  selected: z.boolean().default(false),
});

export const estimateSchema = z.object({
  bench_id: nullableString,
  turns: z.number().catch(0).default(0),
  arms: z.array(estimateArmSchema).catch([]).default([]),
  reps: z.number().catch(1).default(1),
  estimate_usd: z.number().catch(0).default(0),
  run_cap_usd: nullableNumber,
  month_cap_usd: nullableNumber,
  month_spent_usd: nullableNumber,
  month_left_usd: nullableNumber,
  fits: z.boolean().catch(false).default(false),
  reason: nullableString,
  spend_limit_usd: nullableNumber,
});

export const activeStatusSchema = z.object({
  phase: z.string().catch("queued").default("queued"),
  box_phase: nullableString,
  run_id: nullableString,
  arms: z.array(z.string()).catch([]).default([]),
  reps: nullableNumber,
  bench_id: nullableString,
  estimate_usd: nullableNumber,
  started_at_ms: nullableNumber,
  turns_done: nullableNumber,
  turns_total: nullableNumber,
  spent_usd: nullableNumber,
  error: nullableString,
});

export const activeRunSchema = z.object({
  active: activeStatusSchema.nullable().catch(null).default(null),
  /** Cómo terminó la última corrida cuando ya no hay una en curso. */
  last: activeStatusSchema.nullable().catch(null).default(null),
});

export const launchResultSchema = z.object({
  run_id: z.string(),
  workflow_id: z.string().default("lab-launch"),
});

export const cancelResultSchema = z.object({
  cancel_requested: z.boolean().default(false),
  run_id: nullableString,
});

// ── Banco ───────────────────────────────────────────────────────────────────

export const benchReportSchema = z.object({
  bench_id: nullableString,
  counts: z
    .object({
      sessions: z.number().catch(0).default(0),
      cases: z.number().catch(0).default(0),
      excluded_turns: z.number().catch(0).default(0),
    })
    .catch({ sessions: 0, cases: 0, excluded_turns: 0 })
    .default({ sessions: 0, cases: 0, excluded_turns: 0 }),
  exclusions: z
    .array(z.object({ id: z.string(), reason: z.string().default("") }))
    .catch([])
    .default([]),
});

// ── Conversaciones e hilo ───────────────────────────────────────────────────

export const conversationRowSchema = z.object({
  session_id: z.string(),
  turns: z.number().catch(0).default(0),
  episodes: z.array(z.string()).catch([]).default([]),
  last_at_ms: nullableNumber,
  verdicts: z.record(z.string(), z.record(z.string(), episodeVerdictSchema)).catch({}).default({}),
});

export const conversationsSchema = z.object({
  conversations: tolerantArray(conversationRowSchema),
});

export const threadMessageSchema = z.object({
  role: z.string().default("user"),
  content: z.string().catch("").default(""),
  timestamp: nullableString,
  sender: nullableString,
  kind: nullableString,
  component_kind: nullableString,
  wamid: nullableString,
  has_image: z.boolean().catch(false).default(false),
});

export const burstMessageSchema = z.object({
  text: z.string().catch("").default(""),
  ts_ms: nullableNumber,
  wamid: nullableString,
});

export const armOutputSchema = z
  .object({
    sent_texts: z.array(z.string()).catch([]).default([]),
    discarded_narration: z.array(z.string()).catch([]).default([]),
    guards: z.array(z.string()).catch([]).default([]),
    suppressed_reason: nullableString,
    llm_text: nullableString,
  })
  .passthrough();

export const threadTurnSchema = z.object({
  case_id: z.string(),
  turn_key: z.string(),
  episode_id: z.string().default(""),
  turn: z.number().catch(0).default(0),
  at_ms: nullableNumber,
  burst: z.array(burstMessageSchema).catch([]).default([]),
  dashboard_prefix: z.number().catch(0).default(0),
  outputs: z.record(z.string(), armOutputSchema).catch({}).default({}),
});

export const threadEpisodeSchema = z
  .object({
    episode_id: z.string(),
    started_at_ms: nullableNumber,
  })
  .passthrough();

export const threadSchema = z.object({
  session_id: z.string(),
  episode_id: nullableString,
  episodes: z.array(threadEpisodeSchema).catch([]).default([]),
  messages: tolerantArray(threadMessageSchema),
  turns: tolerantArray(threadTurnSchema),
});

// ── Traza de un turno ───────────────────────────────────────────────────────

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
  arm: z.string().default("A0"),
  rep: z.number().catch(0).default(0),
  trace: z.record(z.string(), z.unknown()).catch({}).default({}),
  steps: z.array(traceStepSchema).catch([]).default([]),
});

// ── Evaluaciones ────────────────────────────────────────────────────────────

/** Asunto de una ráfaga y si recibió respuesta (EST-08 v2, registro 3+). */
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
  turn: nullableNumber,
  evidence: nullableString,
  critique: nullableString,
  source: nullableString,
  topics: z.array(topicCoverageSchema).catch([]).default([]),
});

export const episodeEvaluationSchema = z.object({
  session_id: z.string().default(""),
  episode_id: z.string().default(""),
  verdict: episodeVerdictSchema,
  results: z.array(evalResultSchema).catch([]).default([]),
});

export const evaluationsSchema = z.object({
  arm: z.string().default("A0"),
  rep: z.number().catch(0).default(0),
  episodes: z.array(episodeEvaluationSchema).catch([]).default([]),
});
