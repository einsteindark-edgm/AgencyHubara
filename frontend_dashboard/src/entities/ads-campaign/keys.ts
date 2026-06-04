import type { AdsWindowParams } from "./model";

export const adsCampaignKeys = {
  all: ["ads-campaign"] as const,
  // La ventana completa (days|null + from|null + to|null) entra a la key para
  // que cada selección — preset o rango custom — cachee por separado.
  list: (w: AdsWindowParams) =>
    [...adsCampaignKeys.all, "list", w.days, w.from, w.to] as const,
  attributed: (campaignId: string, w: AdsWindowParams) =>
    [...adsCampaignKeys.all, "attributed", campaignId, w.days, w.from, w.to] as const,
  daily: (campaignId: string, w: AdsWindowParams) =>
    [...adsCampaignKeys.all, "daily", campaignId, w.days, w.from, w.to] as const,
} as const;
