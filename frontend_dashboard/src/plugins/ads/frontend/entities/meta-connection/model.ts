/**
 * Modelo de dominio de la conexión a Meta (Marketing API vía app propia).
 *
 * `MetaConnection` = estado del OAuth (botón "Conectar con Meta").
 * `MetaInsights` = métricas REALES por campaña que llenan los slots que el
 * dashboard hoy muestra en "—" (spend/impressions/clicks).
 */

export interface MetaConnection {
  connected: boolean;
  accountId: string | null;
  accountName: string | null;
  scopes: string[];
  expiresAt: number | null;
  /** Token long-lived expirado (~60d) → la UI ofrece reconectar. */
  expired: boolean;
  /** El token tiene scope `ads_management` → habilita pausar/activar. */
  canManage: boolean;
}

export interface MetaInsightsCampaign {
  campaignId: string;
  name: string;
  status: string | null;
  objective: string | null;
  spend: number;
  impressions: number;
  reach: number;
  clicks: number;
  messagingConversationsStarted: number;
}

export interface MetaInsights {
  connected: boolean;
  accountId: string | null;
  accountName: string | null;
  since: string | null;
  until: string | null;
  campaigns: MetaInsightsCampaign[];
}

export interface MetaInsightsParams {
  days?: number;
  since?: string;
  until?: string;
}

/**
 * Qué pedirle a `/api/ads/meta/analysis-input`: la campaña abierta + la ventana del
 * header (preset `days` o rango `from`/`to`). Sin campaña = cuenta completa.
 */
export interface MetaAnalysisInputParams {
  campaignId?: string | null;
  /** `undefined` = sin ventana (14 días); `null` = preset "Total" (se acota a 90). */
  days?: number | null;
  from?: string | null;
  to?: string | null;
}

/** Ruta del seed del análisis (caso Halloween 2026-09-25: se pedía siempre la cuenta
 *  completa de 14 días, aunque el operador mirara una campaña y otro rango). */
export function analysisInputPath(p: MetaAnalysisInputParams): string {
  const q = new URLSearchParams();
  if (p.campaignId) q.set("campaign_id", p.campaignId);
  if (p.from && p.to) {
    q.set("from", p.from);
    q.set("to", p.to);
  } else {
    q.set("days", String(p.days === undefined ? 14 : (p.days ?? 90)));
  }
  return `/api/ads/meta/analysis-input?${q.toString()}`;
}
