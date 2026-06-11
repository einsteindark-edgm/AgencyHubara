import { z } from "zod";

/** Score por métrica dentro del candidato. */
export const evalScoreSchema = z.object({
  metric: z.string(),
  score: z.number(),
  success: z.boolean().optional(),
});

/** Resumen de un candidato a golden (lista). Espeja `_summary` del backend. */
export const evalCandidateSummarySchema = z.object({
  id: z.string(),
  scenario: z.string().default(""),
  status: z.string().default("needs_human_review"),
  source: z.string().default(""),
  /** Sesión + episodio que originaron el candidato ("" en legacy pre-episodio). */
  session_id: z.string().default(""),
  episode_id: z.string().default(""),
  num_turns: z.number().default(0),
  avg_score: z.number().nullable().default(null),
  failed_metrics: z.array(z.string()).default([]),
  expected_outcome_preview: z.string().default(""),
});

export const evalCandidatesListSchema = z.object({
  candidates: z.array(evalCandidateSummarySchema).default([]),
  count: z.number().default(0),
});

/** Turno de la conversación (PII ya redactada por el backend). */
export const evalCandidateTurnSchema = z.object({
  role: z.string(),
  content: z.string(),
});

/** Candidato completo (detalle). */
export const evalCandidateDetailSchema = z.object({
  scenario: z.string().default(""),
  expected_outcome: z.string().default(""),
  turns: z.array(evalCandidateTurnSchema).default([]),
  additional_metadata: z.record(z.string(), z.unknown()).default({}),
});
