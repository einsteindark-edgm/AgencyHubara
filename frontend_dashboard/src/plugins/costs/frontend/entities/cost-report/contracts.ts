/**
 * Schemas Zod de `/api/costs/*` — el boundary HTTP de la pestaña Costos.
 * Espejo del JSON de `src.plugins.costs.api` (snake_case); `api.ts` los usa
 * con `.parse(raw)` y los mappers pasan a camelCase.
 *
 * Tolerancia (L-10): un estado o una fuente que este frontend no conoce no
 * tumba la tarjeta — el estado cae a "error" (se ve, no se esconde) y la
 * fuente a null. Un total ausente se queda en null: nunca se lee como $0.
 */

import { z } from "zod";

const micros = z.number().int();
const nullableMicros = micros.nullable().default(null);

export const backendCatalogSchema = z.object({
  providers: z
    .array(z.object({ id: z.string(), label: z.string(), connected: z.boolean().default(false) }))
    .default([]),
  today: z.string(),
  default_start: z.string(),
  default_end: z.string(),
  max_days: z.number().int().positive().default(92),
});

export const backendCostLineSchema = z.object({
  key: z.string(),
  label: z.string(),
  group: z.string().nullable().default(null),
  currency: z.string().default("USD"),
  amount_micros: micros,
  usd_micros: nullableMicros,
  cop_micros: nullableMicros,
  production: z.boolean().default(true),
  quantity: z.number().nullable().default(null),
  unit: z.string().nullable().default(null),
});

export const backendProviderReportSchema = z.object({
  provider: z.string(),
  status: z.enum(["ok", "not_connected", "error"]).catch("error"),
  source: z.enum(["billed", "metered", "computed"]).nullable().catch(null),
  start: z.string(),
  end: z.string(),
  production_usd_micros: nullableMicros,
  production_cop_micros: nullableMicros,
  other_usd_micros: nullableMicros,
  daily_production_usd_micros: z
    .array(z.object({ date: z.string(), usd_micros: micros }))
    .default([]),
  lines: z.array(backendCostLineSchema).default([]),
  notes: z.array(z.string()).default([]),
  warnings: z.array(z.string()).default([]),
  error: z.string().nullable().default(null),
  how_to_connect: z.string().nullable().default(null),
  fx_note: z.string().nullable().default(null),
  fetched_at: z.string().nullable().default(null),
  cached: z.boolean().default(false),
});

export type BackendCatalog = z.infer<typeof backendCatalogSchema>;
export type BackendCostLine = z.infer<typeof backendCostLineSchema>;
export type BackendProviderReport = z.infer<typeof backendProviderReportSchema>;
