import { z } from "zod";

/**
 * Contratos de GET /api/agents/evals/conversations + /evals/transcript.
 *
 * Tolerantes por diseño (L-10): los campos enumerables que el backend puede
 * extender (closing_tag, trend, role) se validan como string / con `.catch()`
 * en vez de enum estricto — un valor nuevo degrada la presentación, no vacía
 * la sección entera.
 */

/** Una métrica fallada en una eval, con la razón del juez. */
export const failedMetricSchema = z.object({
  metric: z.string(),
  score: z.number(),
  reason: z.string().default(""),
});

/** UNA corrida de evaluación sobre la conversación (un punto del timeline). */
export const episodeEvalRunSchema = z.object({
  date: z.string(),
  ts: z.string().default(""),
  avg: z.number(),
  passed: z.boolean(),
  is_candidate: z.boolean().default(false),
  metrics: z.record(z.string(), z.number()).default({}),
  failed: z.array(failedMetricSchema).default([]),
});

/** Evolución del score entre la primera y la última eval de la conversación. */
export const evalTrendDirectionSchema = z
  .enum(["up", "down", "flat", "single"])
  .catch("single");

/** Una conversación evaluada (sesión + episodio) con su timeline de evals. */
export const conversationEvalSchema = z.object({
  session_id: z.string(),
  /** Vacío = sesión entera (evaluación legacy, sin episodios). */
  episode_id: z.string().default(""),
  evals: z.array(episodeEvalRunSchema).default([]),
  evals_count: z.number().default(0),
  first_avg: z.number().nullable().default(null),
  last_avg: z.number().nullable().default(null),
  last_date: z.string().default(""),
  last_ts: z.string().default(""),
  last_passed: z.boolean().default(true),
  is_candidate: z.boolean().default(false),
  trend: evalTrendDirectionSchema.default("single"),
  failed_metrics: z.array(z.string()).default([]),
  candidate_id: z.string().default(""),
  /** "" = sin candidato · "pending" = esperando curación · "approved" = aprobado. */
  candidate_status: z.string().default(""),
  closing_tag: z.string().nullable().default(null),
  order_id: z.string().nullable().default(null),
});

/** Respuesta de GET /api/agents/evals/conversations. */
export const conversationEvalsSchema = z.object({
  threshold: z.number().default(0.7),
  suite: z.string().default("online"),
  count: z.number().default(0),
  conversations: z.array(conversationEvalSchema).default([]),
});

/** Turno del transcript (mismo segmento que vio el juez, PII redactada). */
export const transcriptTurnSchema = z.object({
  role: z.string(),
  content: z.string(),
  tools: z.array(z.string()).default([]),
});

/** Respuesta de GET /api/agents/evals/transcript. */
export const evalTranscriptSchema = z.object({
  session_id: z.string(),
  episode_id: z.string().default(""),
  turns: z.array(transcriptTurnSchema).default([]),
  truncated_at_human_takeover: z.boolean().default(false),
  episode: z
    .object({
      closing_tag: z.string().nullable().default(null),
      closing_motivo: z.string().nullable().default(null),
      started_at_ms: z.number().nullable().default(null),
      closed_at_ms: z.number().nullable().default(null),
      order_id: z.string().nullable().default(null),
    })
    .nullable()
    .default(null),
});
