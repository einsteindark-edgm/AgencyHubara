export const adsCampaignKeys = {
  all: ["ads-campaign"] as const,
  list: () => [...adsCampaignKeys.all, "list"] as const,
  attributed: (campaignId: string) =>
    [...adsCampaignKeys.all, "attributed", campaignId] as const,
  daily: (campaignId: string) =>
    [...adsCampaignKeys.all, "daily", campaignId] as const,
} as const;
