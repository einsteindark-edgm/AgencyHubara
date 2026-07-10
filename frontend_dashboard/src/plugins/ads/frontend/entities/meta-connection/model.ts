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
