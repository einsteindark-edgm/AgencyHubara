import { z } from "zod";

/**
 * Contratos del scorecard por etapa — `/api/agents/evals/{checks,scorecards,
 * scorecard,scorecard/rescore}`.
 *
 * Tolerantes por diseño (L-10): los enumerables que el backend puede extender
 * (veredicto, nivel, tipo, fidelidad) degradan con `.catch()` a un valor
 * neutro en vez de vaciar la sección entera; etapas, triggers y señales viajan
 * como string libre y la presentación los etiqueta con fallback.
 */

// ── Enumerables ────────────────────────────────────────────────────────────

/** Veredicto del episodio: FALLA si cae un crítico, ALERTA si cae un mayor. */
export const episodeVerdictSchema = z
  .enum(["FALLA", "ALERTA", "PASA", "SIN_DATOS"])
  .catch("SIN_DATOS");

/** Resultado de UN check sobre el episodio. */
export const checkVerdictSchema = z
  .enum(["pasa", "falla", "no_aplica", "desconocido"])
  .catch("desconocido");

export const checkLevelSchema = z.enum(["critico", "mayor", "menor"]).catch("menor");

export const checkKindSchema = z.enum(["code", "judge"]).catch("code");

/** trace = traza por turno completa · legacy = reconstrucción parcial · empty = sin datos. */
export const fidelitySchema = z.enum(["trace", "legacy", "empty"]).catch("empty");

// ── Registro de checks ─────────────────────────────────────────────────────

export const checkFamilySchema = z.object({
  id: z.string(),
  label: z.string().default(""),
  stage: z.string().default("transversal"),
});

export const checkDefinitionSchema = z.object({
  id: z.string(),
  name: z.string().default(""),
  family: z.string().default(""),
  family_label: z.string().default(""),
  stage: z.string().default("transversal"),
  level: checkLevelSchema,
  kind: checkKindSchema,
  applies: z.string().default(""),
  rule: z.string().default(""),
  origin: z.array(z.string()).default([]),
  golden_behaviors: z.array(z.string()).default([]),
  twin_of: z.string().nullable().default(null),
});

/** Respuesta de GET /api/agents/evals/checks. */
export const checkRegistrySchema = z.object({
  registry_version: z.number().default(0),
  stages: z.array(z.string()).default([]),
  families: z.array(checkFamilySchema).default([]),
  checks: z.array(checkDefinitionSchema).default([]),
});

// ── Scorecard (fila de lista) ──────────────────────────────────────────────

export const failurePointSchema = z.object({
  turn: z.number().nullable().default(null),
  check_id: z.string(),
});

export const verdictCountsSchema = z.object({
  critico: z.number().default(0),
  mayor: z.number().default(0),
  menor: z.number().default(0),
  pasa: z.number().default(0),
  no_aplica: z.number().default(0),
  desconocido: z.number().default(0),
});

const EMPTY_COUNTS = {
  critico: 0,
  mayor: 0,
  menor: 0,
  pasa: 0,
  no_aplica: 0,
  desconocido: 0,
};

export const scorecardRowSchema = z.object({
  session_id: z.string(),
  episode_id: z.string().default(""),
  /** Fecha de la evaluación (archivo del store). */
  date: z.string().default(""),
  /** Fecha UTC del episodio (cierre o inicio): la que se muestra y ventanea. */
  episode_date: z.string().nullable().default(null),
  ts: z.string().default(""),
  verdict: episodeVerdictSchema,
  fidelity: fidelitySchema,
  counts: verdictCountsSchema.catch(EMPTY_COUNTS),
  /** pasados / aplicables — secundario, nunca titular. */
  compliance: z.number().nullable().default(null),
  first_failure: failurePointSchema.nullable().catch(null),
  first_critical: failurePointSchema.nullable().catch(null),
  stage_final: z.string().nullable().default(null),
  closing_tag: z.string().nullable().default(null),
  turns: z.number().default(0),
  judge: z.boolean().default(false),
  checks: z.record(z.string(), checkVerdictSchema).catch({}),
});

/** Respuesta de GET /api/agents/evals/scorecards?days=N. */
export const scorecardListSchema = z.object({
  days: z.number().default(30),
  count: z.number().default(0),
  registry_version: z.number().default(0),
  scorecards: z.array(scorecardRowSchema).default([]),
});

// ── Detalle: resultados + trayectoria ──────────────────────────────────────

/** Cobertura de UN asunto del cliente (EST-08 v2): turno, mensaje de la ráfaga y si se atendió. */
export const topicCoverageSchema = z.object({
  topic: z.string(),
  turn: z.number().nullable().default(null),
  msg: z.number().nullable().default(null),
  covered: z.boolean().default(false),
  evidence: z.string().default(""),
});

export const checkResultSchema = z.object({
  check_id: z.string(),
  verdict: checkVerdictSchema,
  level: checkLevelSchema,
  turn: z.number().nullable().default(null),
  evidence: z.string().default(""),
  critique: z.string().default(""),
  source: checkKindSchema,
  /** Solo EST-08 v2 (registro 3+); los resultados anteriores no la traen. */
  topics: z.array(topicCoverageSchema).catch([]).default([]),
});

export const trajectoryToolSchema = z.object({
  name: z.string(),
  ok: z.boolean().nullable().default(null),
  error: z.string().nullable().default(null),
  notes: z.array(z.string()).catch([]),
  args: z.record(z.string(), z.unknown()).catch({}),
});

export const stateChangeSchema = z.object({
  tag: z.string().nullable().default(null),
  source: z.string().nullable().default(null),
  reason: z.string().nullable().default(null),
});

const EMPTY_STATE = {
  tag: null,
  route: null,
  escalation_reason: null,
  closing_tag: null,
  changes: [],
};

/** Estado al cierre del turno. Abierto (`looseObject`): el backend puede sumar claves. */
export const turnStateSchema = z.looseObject({
  tag: z.string().nullable().catch(null),
  route: z.string().nullable().catch(null),
  escalation_reason: z.string().nullable().catch(null),
  closing_tag: z.string().nullable().catch(null),
  changes: z.array(stateChangeSchema).catch([]),
});

export const trajectoryTurnSchema = z.object({
  turn: z.number(),
  at_ms: z.number().nullable().default(null),
  /** customer · ghost · handoff (string libre: valores nuevos degradan la etiqueta). */
  trigger: z.string().default("customer"),
  inbound_text: z.string().nullable().default(null),
  /** deferral · affirmation · null. */
  signal: z.string().nullable().default(null),
  sent_texts: z.array(z.string()).catch([]),
  llm_text: z.string().nullable().default(null),
  suppressed_reason: z.string().nullable().default(null),
  discarded_narration: z.array(z.string()).catch([]),
  tools: z.array(trajectoryToolSchema).catch([]),
  intents: z.array(z.string()).catch([]),
  guards: z.array(z.string()).catch([]),
  stage_in: z.string().nullable().default(null),
  stage_out: z.string().nullable().default(null),
  draft: z.record(z.string(), z.unknown()).nullable().catch(null),
  confirmed: z.boolean().nullable().default(null),
  state: turnStateSchema.catch(EMPTY_STATE),
  first_contact: z.boolean().nullable().default(null),
});

export const trajectorySchema = z.object({
  session_id: z.string().default(""),
  episode_id: z.string().default(""),
  fidelity: fidelitySchema,
  closing_tag: z.string().nullable().default(null),
  order_id: z.string().nullable().default(null),
  closing_motivo: z.string().nullable().default(null),
  started_at_ms: z.number().nullable().default(null),
  closed_at_ms: z.number().nullable().default(null),
  turns: z.array(trajectoryTurnSchema).default([]),
});

export const legacyScoreSchema = z.object({
  avg: z.number(),
  date: z.string().default(""),
  metrics: z.record(z.string(), z.number()).catch({}),
});

export const scorecardWithResultsSchema = scorecardRowSchema.extend({
  results: z.array(checkResultSchema).default([]),
});

/** Respuesta de GET /api/agents/evals/scorecard y de POST scorecard/rescore. */
export const scorecardDetailSchema = z.object({
  stored: z.boolean().default(false),
  scorecard: scorecardWithResultsSchema.nullable().default(null),
  trajectory: trajectorySchema.nullable().default(null),
  legacy: legacyScoreSchema.nullable().catch(null),
  /** Solo en la respuesta del recálculo con juez: si el worker lo encoló. */
  judge_queued: z.boolean().optional(),
  judge_workflow_id: z.string().optional(),
  judge_error: z.string().optional(),
});
