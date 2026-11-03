import type { ReviewStatus } from "./model";

/** Query keys de las reseñas — las comparten todas las features del plugin. */
export const reviewKeys = {
  all: ["reviews-review"] as const,
  lists: () => [...reviewKeys.all, "list"] as const,
  list: (status: ReviewStatus) => [...reviewKeys.lists(), status] as const,
  summary: () => [...reviewKeys.all, "summary"] as const,
} as const;
