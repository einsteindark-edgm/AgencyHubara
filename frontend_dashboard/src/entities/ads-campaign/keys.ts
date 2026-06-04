export const adsCampaignKeys = {
  all: ["ads-campaign"] as const,
  // `days` (o null = todo) entra a la key para que cada ventana cachee aparte.
  list: (days: number | null = null) =>
    [...adsCampaignKeys.all, "list", days] as const,
  attributed: (campaignId: string, days: number | null = null) =>
    [...adsCampaignKeys.all, "attributed", campaignId, days] as const,
  daily: (campaignId: string, days: number | null = null) =>
    [...adsCampaignKeys.all, "daily", campaignId, days] as const,
} as const;
