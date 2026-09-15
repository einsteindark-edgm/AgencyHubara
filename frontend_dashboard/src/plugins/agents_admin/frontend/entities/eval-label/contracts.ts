import { z } from "zod";

import {
  checkLevelSchema,
  checkVerdictSchema,
} from "@plugins/agents_admin/frontend/entities/scorecard";

/**
 * Contratos de las etiquetas humanas (Capa 3 del scorecard): `/api/agents/evals/
 * {labels, labels/queue, calibration}`. Una etiqueta es el veredicto del
 * operador (pasa/falla) sobre un check de un episodio; contra ellas se mide si
 * el juez LLM es confiable (TPR/TNR/kappa). Tolerantes (L-10).
 */

export const evalLabelSchema = z.object({
  session_id: z.string(),
  episode_id: z.string().default(""),
  check_id: z.string(),
  /** "pasa" | "falla" — string libre para no vaciar la lista ante un valor nuevo. */
  verdict: z.string(),
  note: z.string().default(""),
  labeled_at: z.string().default(""),
});

export const labelsListSchema = z.object({
  labels: z.array(evalLabelSchema).default([]),
});

export const createLabelResponseSchema = z.object({
  ok: z.boolean().default(true),
  label: evalLabelSchema,
});

export const labelQueueItemSchema = z.object({
  session_id: z.string(),
  episode_id: z.string().default(""),
  check_id: z.string(),
  check_name: z.string().default(""),
  judge_verdict: checkVerdictSchema,
  /** desconocido · falla · muestra. */
  reason: z.string().default("muestra"),
  evidence: z.string().default(""),
  critique: z.string().default(""),
});

export const labelQueueSchema = z.object({
  items: z.array(labelQueueItemSchema).default([]),
});

export const calibrationStatusSchema = z
  .enum(["confiable", "revisar", "sin_datos"])
  .catch("sin_datos");

export const calibrationCheckSchema = z.object({
  check_id: z.string(),
  name: z.string().default(""),
  level: checkLevelSchema,
  n: z.number().default(0),
  tp: z.number().default(0),
  fp: z.number().default(0),
  tn: z.number().default(0),
  fn: z.number().default(0),
  tpr: z.number().nullable().default(null),
  tnr: z.number().nullable().default(null),
  kappa: z.number().nullable().default(null),
  status: calibrationStatusSchema,
});

export const calibrationSchema = z.object({
  min_labels: z.number().default(0),
  kappa_threshold: z.number().default(0),
  checks: z.array(calibrationCheckSchema).default([]),
});
