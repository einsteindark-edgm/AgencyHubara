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

/** Veredicto del episodio (mismo enum que Calidad LLM). */
export const episodeVerdictSchema = z.enum(["FALLA", "ALERTA", "PASA", "SIN_DATOS"]).catch("SIN_DATOS");

/** Resultado de un check. `sin_senal`: en modo turno, el check depende de
 * turnos posteriores y el turno solo no alcanza para decidir (PR 12). */
export const checkVerdictSchema = z
  .enum(["pasa", "falla", "no_aplica", "desconocido", "sin_senal"])
  .catch("desconocido");

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
});

export const runsSchema = z.object({ runs: z.array(runSchema).catch([]).default([]) });

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

export const activeRunSchema = z.object({ active: activeStatusSchema.nullable().catch(null).default(null) });

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
  conversations: z.array(conversationRowSchema).catch([]).default([]),
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
  messages: z.array(threadMessageSchema).catch([]).default([]),
  turns: z.array(threadTurnSchema).catch([]).default([]),
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


// ── Resumen de la corrida (PR 13) ──────────────────────────────────────────

const rate = z.number().nullable().catch(null).default(null);
const count = z.number().catch(0).default(0);

const verdictCountsSchema = z
  .object({ FALLA: count, ALERTA: count, PASA: count, SIN_DATOS: count })
  .catch({ FALLA: 0, ALERTA: 0, PASA: 0, SIN_DATOS: 0 });

const levelSchema = z.enum(["critico", "mayor", "menor"]).catch("menor");

export const passKSchema = z.object({ k: count, episodes: count, rate });

/** Un brazo: la MISMA forma que Calidad LLM (`stats.compute_stats`) + pass^k. */
export const armSummarySchema = z.object({
  reps: count,
  mode: z.string().catch("episode").default("episode"),
  episodes: count,
  verdicts: verdictCountsSchema.default({ FALLA: 0, ALERTA: 0, PASA: 0, SIN_DATOS: 0 }),
  pareto: z
    .array(z.object({ check_id: z.string(), name: z.string().catch("").default(""), level: levelSchema, failures: count }))
    .catch([])
    .default([]),
  trend: z
    .array(
      z.object({
        check_id: z.string(),
        name: z.string().catch("").default(""),
        level: levelSchema,
        weeks: z.array(z.object({ week: z.string(), applicable: count, passed: count, rate })).catch([]).default([]),
      }),
    )
    .catch([])
    .default([]),
  funnel: z
    .array(z.object({ stage: z.string().catch("sin_etapa"), FALLA: count, ALERTA: count, PASA: count, SIN_DATOS: count }))
    .catch([])
    .default([]),
  pass_k: passKSchema.nullable().catch(null).default(null),
});

export const intervalSchema = z.object({
  delta: rate,
  low: rate,
  high: rate,
  conclusive: z.boolean().catch(false).default(false),
  sessions: count,
});

export const runDiffSchema = z.object({
  base: z.string(),
  cand: z.string(),
  episode_pass: intervalSchema.catch({ delta: null, low: null, high: null, conclusive: false, sessions: 0 }),
  pass_k: z
    .object({ base: passKSchema, cand: passKSchema })
    .nullable()
    .catch(null)
    .default(null),
  checks: z.array(intervalSchema.extend({ check_id: z.string() })).catch([]).default([]),
  changed_turns: z
    .array(
      z.object({
        session_id: z.string(),
        episode_id: z.string(),
        turn: count,
        base: episodeVerdictSchema,
        cand: episodeVerdictSchema,
        checks: z.array(z.string()).catch([]).default([]),
      }),
    )
    .catch([])
    .default([]),
});

export const armMetricsSchema = z.object({
  rep: count,
  turns: count,
  errors: count,
  perception: z
    .object({ turns: count, fallback_rate: rate, p50_ms: rate, p95_ms: rate })
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
  cost_per_turn_usd: rate,
  perception_cost_per_turn_usd: rate,
});

const calibrationSchema = z
  .object({ n: count, brier: rate, ece: rate })
  .catch({ n: 0, brier: null, ece: null });

export const arenaArmSchema = z.object({
  profile: z.string().nullable().catch(null).default(null),
  metrics: z.array(armMetricsSchema).catch([]).default([]),
  topics: z
    .object({ turns: count, precision: rate, recall: rate, f1: rate, calibration: calibrationSchema.default({ n: 0, brier: null, ece: null }) })
    .catch({ turns: 0, precision: null, recall: null, f1: null, calibration: { n: 0, brier: null, ece: null } })
    .default({ turns: 0, precision: null, recall: null, f1: null, calibration: { n: 0, brier: null, ece: null } }),
  unmapped_judge_topics: count,
});

export const runReportSchema = z.object({
  run_id: z.string().nullable().catch(null).default(null),
  mode: z.string().catch("episode").default("episode"),
  registry_version: rate,
  arms: z.array(z.string()).catch([]).default([]),
  arms_pending: z.array(z.string()).catch([]).default([]),
  production: armSummarySchema.nullable().catch(null).default(null),
  fidelity: z
    .object({ n: count, agreement: rate, threshold: z.number().catch(0.9).default(0.9), ok: z.boolean().catch(false).default(false) })
    .nullable()
    .catch(null)
    .default(null),
  validation: z
    .object({
      episodes: count,
      prod_registry_versions: z.array(z.number()).catch([]).default([]),
      agreement: rate,
      verdict_agreement: rate,
    })
    .nullable()
    .catch(null)
    .default(null),
  arena: z.record(z.string(), arenaArmSchema).catch({}).default({}),
  judge: z
    .object({ used: z.boolean().catch(false).default(false), errors: count })
    .nullable()
    .catch(null)
    .default(null),
  diffs: z.array(z.string()).catch([]).default([]),
});
