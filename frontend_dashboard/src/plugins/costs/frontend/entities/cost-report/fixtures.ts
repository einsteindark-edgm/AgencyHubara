/**
 * JSON con la forma EXACTA que devuelve `src.plugins.costs.api` (snake_case),
 * con cifras inventadas: este repo es público, las cifras reales no van acá.
 */

export const CATALOG_FIXTURE = {
  providers: [
    { id: "deepseek", label: "DeepSeek", connected: true },
    { id: "temporal", label: "Temporal Cloud", connected: false },
    { id: "whatsapp", label: "WhatsApp (Meta)", connected: true },
  ],
  today: "2026-11-04",
  default_start: "2026-10-22",
  default_end: "2026-11-04",
  max_days: 92,
};

export const DEEPSEEK_REPORT_FIXTURE = {
  provider: "deepseek",
  status: "ok",
  source: "conversations",
  start: "2026-10-22",
  end: "2026-11-04",
  production_usd_micros: 7_500_000,
  production_cop_micros: 24_000_000_000,
  other_usd_micros: null,
  daily_production_usd_micros: [
    { date: "2026-10-22", usd_micros: 5_000_000 },
    { date: "2026-10-23", usd_micros: 2_500_000 },
  ],
  lines: [
    {
      key: "llm", label: "Turnos del bot (ventas y remarketing)", group: null, currency: "USD",
      amount_micros: 7_500_000, usd_micros: 7_500_000, cop_micros: 24_000_000_000,
      production: true, quantity: 1_200_000, unit: "tokens",
    },
  ],
  notes: ["Suma de 40 conversaciones que empezaron en el rango."],
  warnings: [],
  error: null,
  how_to_connect: null,
  fx_note: "Pesos ↔ dólares con la TRM oficial de cada día (Superintendencia Financiera).",
  fetched_at: "2026-11-04T15:00:00+00:00",
  cached: false,
};

export const NOT_CONNECTED_FIXTURE = {
  provider: "temporal",
  status: "not_connected",
  source: null,
  start: "2026-10-22",
  end: "2026-11-04",
  production_usd_micros: null,
  production_cop_micros: null,
  other_usd_micros: null,
  daily_production_usd_micros: [],
  lines: [],
  notes: [],
  warnings: [],
  error: null,
  how_to_connect: "Falta TEMPORAL_API_KEY en el entorno del API.",
  fx_note: null,
  fetched_at: "2026-11-04T15:00:00+00:00",
  cached: true,
};

export const WHATSAPP_REPORT_FIXTURE = {
  ...DEEPSEEK_REPORT_FIXTURE,
  provider: "whatsapp",
  source: "metered",
  production_usd_micros: 1_000_000,
  production_cop_micros: 3_200_000_000,
  other_usd_micros: null,
  daily_production_usd_micros: [{ date: "2026-10-22", usd_micros: 1_000_000 }],
  lines: [
    {
      key: "MARKETING:REGULAR", label: "Marketing", group: "Categoría de Meta", currency: "COP",
      amount_micros: 3_200_000_000, usd_micros: 1_000_000, cop_micros: 3_200_000_000,
      production: true, quantity: 67, unit: "mensajes",
    },
  ],
};
