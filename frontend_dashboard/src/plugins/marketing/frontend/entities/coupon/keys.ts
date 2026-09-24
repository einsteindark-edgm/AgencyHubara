export const couponKeys = {
  all: ["marketing-coupon"] as const,
  list: () => [...couponKeys.all, "list"] as const,
  detail: (promotionId: string) =>
    [...couponKeys.all, "detail", promotionId] as const,
  sales: (promotionId: string) =>
    [...couponKeys.all, "sales", promotionId] as const,
  products: () => [...couponKeys.all, "products"] as const,
} as const;
