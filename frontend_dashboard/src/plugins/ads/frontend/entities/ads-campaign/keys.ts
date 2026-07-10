import type { AdsWindowParams } from "./model";

export const adsCampaignKeys = {
  all: ["ads-campaign"] as const,
  // La ventana completa (days|null + from|null + to|null) entra a la key para
  // que cada selección — preset o rango custom — cachee por separado.
  list: (w: AdsWindowParams) =>
    [...adsCampaignKeys.all, "list", w.days, w.from, w.to] as const,
  // `adsetId` (o null = campaña completa) entra a la key: el drill-down por
  // segmento cachea aparte del de la campaña.
  attributed: (campaignId: string, w: AdsWindowParams, adsetId: string | null = null) =>
    [...adsCampaignKeys.all, "attributed", campaignId, adsetId, w.days, w.from, w.to] as const,
  daily: (campaignId: string, w: AdsWindowParams, adsetId: string | null = null) =>
    [...adsCampaignKeys.all, "daily", campaignId, adsetId, w.days, w.from, w.to] as const,
  adsets: (campaignId: string, w: AdsWindowParams) =>
    [...adsCampaignKeys.all, "adsets", campaignId, w.days, w.from, w.to] as const,
} as const;
