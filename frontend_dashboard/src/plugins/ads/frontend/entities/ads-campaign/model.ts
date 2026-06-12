/**
 * Entidad "ads-campaign" — campañas Meta Ads → WhatsApp.
 *
 * Modela una campaña activa/pausada con sus métricas Meta (impresiones, clics,
 * inversión) + el embudo WhatsApp (chats por estado conversacional + ingresos
 * atribuidos). Los plugins solo leen este modelo; el avance de la campaña
 * (cambios de estado) es responsabilidad del backend Meta sync (fuera de scope
 * del plugin frontend-only de hoy).
 */

/* ── Estados de conversación específicos del embudo de ads ──────────────── */

export type AdsState =
  | "no_reply"
  | "nuevo"
  | "activo"
  | "calificado"
  | "cotizado"
  | "ganado"
  | "perdido";

export interface AdsStateMeta {
  key: AdsState;
  label: string;
  color: string;
  bg: string;
}

export const ADS_STATES: Record<AdsState, AdsStateMeta> = {
  no_reply:   { key: "no_reply",   label: "Sin respuesta",    color: "var(--color-neutral)", bg: "rgba(142,142,147,0.22)" },
  nuevo:      { key: "nuevo",      label: "Nuevo",            color: "var(--color-info)", bg: "rgba(95,169,255,0.22)" },
  activo:     { key: "activo",     label: "En conversación",  color: "var(--color-cyan)", bg: "rgba(95,220,255,0.22)" },
  calificado: { key: "calificado", label: "Calificado",       color: "var(--color-violet)", bg: "rgba(214,138,255,0.22)" },
  cotizado:   { key: "cotizado",   label: "Cotizado",         color: "var(--color-warn)", bg: "rgba(255,180,74,0.22)" },
  ganado:     { key: "ganado",     label: "Ganado",           color: "var(--color-ok)", bg: "rgba(91,224,123,0.22)" },
  perdido:    { key: "perdido",    label: "Perdido",          color: "var(--color-danger)", bg: "rgba(255,114,105,0.22)" },
};

export const ADS_STATE_ORDER: AdsState[] = [
  "no_reply",
  "nuevo",
  "activo",
  "calificado",
  "cotizado",
  "ganado",
  "perdido",
];

/* ── Campaña ───────────────────────────────────────────────────────────── */

export type CampaignStatus = "active" | "paused";
export type CampaignTendency = "up" | "flat" | "down";

/* ── Rango temporal del dashboard (filtro por fecha) ─────────────────────── */

/**
 * Ventana temporal del dashboard ads. Se empuja al backend (`?days=N`) para que
 * la agregación (revenue/costo LLM/counts) y el scan del vault escalen con la
 * ventana, no con todo el historial — `total` agrega todo (más caro).
 */
export type AdsDateRange = "7d" | "30d" | "90d" | "total";

export const ADS_DATE_RANGES: { key: AdsDateRange; label: string; days: number | null }[] = [
  { key: "7d", label: "7d", days: 7 },
  { key: "30d", label: "30d", days: 30 },
  { key: "90d", label: "90d", days: 90 },
  { key: "total", label: "Total", days: null },
];

/** Días de la ventana, o `null` para `total` (sin límite inferior). */
export function rangeDays(r: AdsDateRange): number | null {
  return ADS_DATE_RANGES.find((x) => x.key === r)?.days ?? null;
}

/**
 * Selección de ventana del dashboard, discriminada por `kind`:
 *  - `preset`: uno de los atajos 7d/30d/90d/total (límite inferior relativo a hoy).
 *  - `custom`: rango calendario explícito `[from, to]` (YYYY-MM-DD, America/Bogota),
 *    ambos inclusive — para analizar un mes/trimestre puntual con precisión.
 * Los presets cubren el día a día; el rango custom da exactitud (y acota el
 * cómputo a una ventana fija).
 */
export type AdsRangeSelection =
  | { kind: "preset"; preset: AdsDateRange }
  | { kind: "custom"; from: string; to: string };

/** Selección por defecto — preset acotado (no escanea todo el historial). */
export const DEFAULT_ADS_SELECTION: AdsRangeSelection = {
  kind: "preset",
  preset: "30d",
};

/**
 * Parámetros de query que la selección empuja al backend. El backend acepta
 * `days` (preset; límite inferior = hoy − días) O `from`/`to` (rango custom,
 * ambos inclusive); `from`/`to` ganan sobre `days`. Mantener `days` además del
 * rango deja que el caller derive la ventana del gráfico diario sin re-derivar.
 */
export interface AdsWindowParams {
  days: number | null;
  from: string | null;
  to: string | null;
}

/** Traduce una selección a los parámetros de query del backend. */
export function selectionToParams(sel: AdsRangeSelection): AdsWindowParams {
  if (sel.kind === "custom") {
    // Normalizamos el orden (YYYY-MM-DD compara cronológicamente como string).
    const [from, to] =
      sel.from <= sel.to ? [sel.from, sel.to] : [sel.to, sel.from];
    return { days: null, from, to };
  }
  return { days: rangeDays(sel.preset), from: null, to: null };
}

export type AdsConversationCounts = Record<AdsState, number>;

/**
 * Una campaña Meta vista desde el dashboard. Muchos campos son `| null`
 * porque hoy solo tenemos lo que clasifica el ingest WhatsApp (origin +
 * referrals) + lo congelado por episodio (revenue/llm/duración). La
 * integración con Meta Ads API (spend/impressions/clicks) viene en PRs
 * futuros — los components muestran "—" + icono dataPending para los `null`.
 * Los hooks (`useAdsCampaigns`) hacen fetch real y mapean el backend.
 */
export interface AdsCampaign {
  // --- Disponibles hoy (derivados del ingest WhatsApp) ---
  id: string;
  name: string | null;
  /** Chats iniciados (CTWA) — count de sesiones con este source_id. */
  started: number;
  /** Rango formateado ("1 may → 14 may 2026"). "—" si no hay timestamps. */
  dates: string;

  // --- Faltantes (queda null hasta integración Meta Ads / orders) ---
  status: CampaignStatus | null;
  objective: string | null;
  placement: string | null;
  audience: string | null;
  daysRun: number | null;
  metaCampaignId: string | null;
  adSet: string | null;
  creativeTitle: string | null;
  template: string | null;
  /** Inversión en COP. */
  spend: number | null;
  impressions: number | null;
  reach: number | null;
  clicks: number | null;
  /** Counts por estado conversacional — requiere clasificador downstream. */
  conversations: AdsConversationCounts | null;
  /** Ingresos atribuidos en COP — suma de `order_total_cop` de los episodios
   *  ganados del bucket. null si el bucket no tuvo ventas con total conocido. */
  revenue: number | null;
  /** Ticket promedio en COP (revenue / nº de episodios que aportaron ingreso). */
  avgTicket: number | null;
  /** Costo LLM agregado de la campaña en USD (suma de `episode.llm_usage` de
   *  todos los episodios del bucket). null si ningún episodio acumuló uso. */
  llmCostUsd: number | null;
  /** Tokens LLM totales (prompt+completion) de la campaña. */
  llmTokens: number | null;
  /** Duración media de los episodios CERRADOS del bucket (ms) — el "tiempo"
   *  del embudo. null si no hay episodios cerrados con timestamps válidos. */
  avgEpisodeDurationMs: number | null;
  /** Tiempo a 1ª respuesta del bot (texto formateado tipo "2m 14s"). */
  firstResp: string | null;
  tendency: CampaignTendency | null;
}

/* ── Conversaciones atribuidas (WhatsApp chats originados por un anuncio) ── */

export type AvatarColor = "a" | "b" | "c" | "d" | "e" | "f";

/**
 * Conversación WhatsApp atribuida a una campaña. Los campos `| null` los
 * marca el frontend con "—" + icono dataPending — la mayor parte requiere
 * integraciones futuras (CRM para `name`/`city`, clasificador para
 * `state`, orders para `value`).
 */
export interface AttributedConversation {
  // --- Disponibles hoy ---
  /** ID único de la conversación. Formato:
   *  - `wa_<phone>__<episode_id>` cuando viene del modelo nuevo (episodios).
   *  - `wa_<phone>` para sesiones legacy sin episodios.
   *  Varias filas pueden compartir `phone_number` (mismo cliente, distintas
   *  conversaciones a lo largo del tiempo). */
  id: string;
  /** ID del episodio dentro de la sesión. `null` para legacy. Opcional
   *  para tolerar el mock histórico (que no lo declara). */
  episodeId?: string | null;
  /** Iniciales 2-letras — derivadas del nombre o del phone si no hay nombre. */
  short: string;
  color: AvatarColor;
  /** "Hoy 11:42", "Ayer 19:02", "9 may" — derivado de started_at_ms. */
  started: string;
  msgs: number;

  // --- Disponibles parcialmente / faltantes ---
  /** Nombre del cliente. Null hasta CRM — la UI muestra phone. */
  name: string | null;
  /** Null hasta CRM. */
  city: string | null;
  /** "Sofía" / "Diego" / "IA Soporte" / null. Backend devuelve `active_route` como proxy. */
  agent: string | null;
  /** Null hasta clasificador conversacional. */
  state: AdsState | null;
  /** Valor de la venta atribuida al episodio en COP — de `episode.order_total_cop`
   *  congelado al cierre (backfill desde registered_order). Null si no cerró venta. */
  value: number | null;
  /** "Hoy 12:18" derivado de last_msg_at_ms. Null si no hubo último mensaje rastreable. */
  lastMsg: string | null;
  /** Nombre del anuncio que originó el chat (headline). */
  ad: string | null;
  /** Duración del episodio (ms) = closed − started. Null si sigue activo o sin
   *  timestamps. Opcional para tolerar el mock histórico. */
  durationMs?: number | null;

  // --- Costo LLM del episodio (HU costo por venta) ---
  /** Costo LLM del episodio en USD (congelado a la tarifa del momento). `null`
   *  si el episodio aún no acumuló uso. Opcional para tolerar el mock histórico. */
  llmCostUsd?: number | null;
  /** Tokens totales (prompt+completion) del episodio. */
  llmTokens?: number | null;
}

/* ── Serie diaria — conversaciones iniciadas por día/estado final ────────── */

export interface AdsDailyPoint {
  /** Etiqueta del día ("12 may"). */
  d: string;
  ganado: number;
  cotizado: number;
  calificado: number;
  activo: number;
  nuevo: number;
  no_reply: number;
  perdido: number;
}

/* ── Helpers de dominio ─────────────────────────────────────────────────── */

/**
 * Suma todos los estados conversacionales de una campaña. Si la campaña
 * aún no tiene counts (backend devuelve `conversations: null` hasta el
 * clasificador conversacional), devolvemos `c.started` como fallback —
 * al menos sabemos cuántos chats arrancaron por CTWA.
 */
export function totalConversations(c: AdsCampaign): number {
  if (!c.conversations) return c.started;
  return ADS_STATE_ORDER.reduce((acc, s) => acc + (c.conversations![s] || 0), 0);
}
