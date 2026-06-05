import { z } from "zod";

/** Un punto de la serie: el agregado de una métrica en un día. */
export const evalTrendPointSchema = z.object({
  date: z.string(),
  avg: z.number(),
  min: z.number(),
  n: z.number(),
  n_below: z.number(),
});

/** La serie temporal de UNA métrica. */
export const evalTrendSeriesSchema = z.object({
  metric: z.string(),
  points: z.array(evalTrendPointSchema).default([]),
});

/** Respuesta de GET /api/chats/evals/history — tendencia por métrica en el tiempo. */
export const evalTrendSchema = z.object({
  threshold: z.number().default(0.7),
  suite: z.string().default("online"),
  metrics: z.array(z.string()).default([]),
  series: z.array(evalTrendSeriesSchema).default([]),
});
