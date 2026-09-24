import { z } from "zod";

// Deep import (no barrel): la entity scorecard importa `checkStatsKeys` desde
// este barrel para su mutation de recálculo; ir por su barrel crearía un ciclo.
import { checkLevelSchema } from "@plugins/agents_admin/frontend/entities/scorecard/contracts";

/**
 * Contrato de GET /api/agents/evals/checks/stats?days=N — agregados del
 * scorecard por etapa: conteo de veredictos, Pareto de fallos, tasa semanal de
 * cumplimiento por check y embudo de etapa terminal. Tolerante (L-10).
 */

export const verdictTotalsSchema = z.object({
  FALLA: z.number().default(0),
  ALERTA: z.number().default(0),
  PASA: z.number().default(0),
  SIN_DATOS: z.number().default(0),
});

const EMPTY_TOTALS = { FALLA: 0, ALERTA: 0, PASA: 0, SIN_DATOS: 0 };

export const paretoItemSchema = z.object({
  check_id: z.string(),
  name: z.string().default(""),
  level: checkLevelSchema,
  failures: z.number().default(0),
});

export const trendWeekSchema = z.object({
  /** Lunes de la semana, YYYY-MM-DD. */
  week: z.string(),
  applicable: z.number().default(0),
  passed: z.number().default(0),
  rate: z.number().nullable().default(null),
});

export const checkTrendSchema = z.object({
  check_id: z.string(),
  name: z.string().default(""),
  level: checkLevelSchema,
  weeks: z.array(trendWeekSchema).default([]),
});

export const funnelRowSchema = verdictTotalsSchema.extend({
  stage: z.string(),
});

export const checkStatsSchema = z.object({
  days: z.number().default(56),
  /** Bot por el que filtró el servidor (PR 18); null = todos o una API anterior al filtro. */
  bot: z.enum(["actual", "nuevo"]).nullable().catch(null).default(null),
  episodes: z.number().default(0),
  verdicts: verdictTotalsSchema.catch(EMPTY_TOTALS),
  pareto: z.array(paretoItemSchema).default([]),
  trend: z.array(checkTrendSchema).default([]),
  funnel: z.array(funnelRowSchema).default([]),
});
