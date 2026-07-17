export const productKeys = {
  all: ["marketing-product"] as const,
  list: () => [...productKeys.all, "list"] as const,
} as const;
