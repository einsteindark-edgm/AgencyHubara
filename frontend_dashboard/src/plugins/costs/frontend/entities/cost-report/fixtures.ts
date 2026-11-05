/**
 * JSON con la forma EXACTA que devuelve `src.plugins.costs.api` (snake_case),
 * con cifras inventadas: este repo es público, las cifras reales no van acá.
 */

export const CATALOG_FIXTURE = {
  providers: [
    { id: "deepseek", label: "DeepSeek", connected: true },
    { id: "openrouter", label: "OpenRouter (Jev)", connected: false },
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
  source: "metered",
  start: "2026-10-22",
  end: "2026-11-04",
  production_usd_micros: 7_500_000,
  production_cop_micros: 24_000_000_000,
  other_usd_micros: 1_250_000,
  daily_production_usd_micros: [
    { date: "2026-10-22", usd_micros: 5_000_000 },
    { date: "2026-10-23", usd_micros: 2_500_000 },
  ],
  lines: [
    {
      key: "t-prod:USD", label: "Hubara Ventas", group: "Llave de API", currency: "USD",
      amount_micros: 7_000_000, usd_micros: 7_000_000, cop_micros: 22_400_000_000,
      production: true, quantity: 900, unit: "solicitudes",
    },
    {
      key: "t-prod:CNY", label: "Hubara Ventas", group: "Llave de API", currency: "CNY",
      amount_micros: 3_550_000, usd_micros: 500_000, cop_micros: 1_600_000_000,
      production: true, quantity: null, unit: null,
    },
    {
      key: "t-lab:USD", label: "Laboratorios", group: "Llave de API", currency: "USD",
      amount_micros: 1_250_000, usd_micros: 1_250_000, cop_micros: 4_000_000_000,
      production: false, quantity: 300, unit: "solicitudes",
    },
  ],
  notes: ["Cifras de la página Usage de DeepSeek, por llave y por día de Bogotá."],
  warnings: [],
  error: null,
  how_to_connect: null,
  fx_note: "Pesos ↔ dólares con la TRM oficial de cada día (Superintendencia Financiera).",
  fetched_at: "2026-11-04T15:00:00+00:00",
  cached: false,
};

export const NOT_CONNECTED_FIXTURE = {
  provider: "openrouter",
  status: "not_connected",
  source: null,
  start: "2026-10-22",
  end: "2026-11-04",
  production_usd_micros: null,
  production_cop_micros: null,
  other_usd_micros: null,
  daily_production_usd_micros: [],
  lines: [],
  notes: ["La llave del motor solo informa totales sin fechas."],
  warnings: [],
  error: null,
  how_to_connect: "Crea una llave de administración y guárdala como OPENROUTER_MANAGEMENT_KEY.",
  fx_note: null,
  fetched_at: "2026-11-04T15:00:00+00:00",
  cached: true,
};

export const WHATSAPP_REPORT_FIXTURE = {
  ...DEEPSEEK_REPORT_FIXTURE,
  provider: "whatsapp",
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
