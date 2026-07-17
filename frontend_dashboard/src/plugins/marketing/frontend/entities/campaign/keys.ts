export const campaignKeys = {
  all: ["marketing-campaign"] as const,
  list: () => [...campaignKeys.all, "list"] as const,
  stats: (campaignId: string) =>
    [...campaignKeys.all, "stats", campaignId] as const,
} as const;
