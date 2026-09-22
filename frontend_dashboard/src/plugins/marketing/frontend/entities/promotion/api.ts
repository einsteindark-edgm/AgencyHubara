/**
 * Hook de fetching de cupones vigentes (`/api/marketing/promotions`).
 * Zod en el boundary + mapper snake→camel.
 */

import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/sdk";

import {
  backendPromotionsResponseSchema,
  type BackendPromotionsResponse,
} from "./contracts";
import { promotionKeys } from "./keys";
import type { PromotionsInfo } from "./model";

export function mapBackendPromotions(b: BackendPromotionsResponse): PromotionsInfo {
  return {
    promotions: b.promotions.map((p) => ({
      code: p.code,
      discountType: p.discount_type,
      value: p.value,
      targetType: p.target_type,
      name: p.name,
      endsAtMs: p.ends_at_ms,
      minSubtotalCop: p.min_subtotal_cop,
      productCount: p.product_count,
    })),
    unavailable: b.unavailable,
  };
}

/** Cupones vigentes en Medusa — fetch lazy (`enabled`) solo cuando el paso
 *  de oferta está en pantalla. */
export function usePromotions(enabled = true) {
  return useQuery<PromotionsInfo>({
    queryKey: promotionKeys.list(),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>("/api/marketing/promotions", {
        signal,
      });
      return mapBackendPromotions(backendPromotionsResponseSchema.parse(raw));
    },
    staleTime: 60_000,
    enabled,
  });
}
