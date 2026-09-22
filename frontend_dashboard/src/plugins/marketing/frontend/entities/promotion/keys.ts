export const promotionKeys = {
  all: ["marketing-promotion"] as const,
  list: () => [...promotionKeys.all, "list"] as const,
} as const;
