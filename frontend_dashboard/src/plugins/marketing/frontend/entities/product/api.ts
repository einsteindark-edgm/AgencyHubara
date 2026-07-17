/**
 * Hook de fetching del catálogo (`/api/marketing/products`) para el picker.
 * Zod en el boundary + normalización de precio string|number → number|null.
 */

import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/sdk";

import { backendProductsResponseSchema, type BackendProduct } from "./contracts";
import { productKeys } from "./keys";
import type { CatalogProduct } from "./model";

export function mapBackendProduct(b: BackendProduct): CatalogProduct {
  let priceAmount: number | null = null;
  if (b.price_amount !== null) {
    const n = Number(b.price_amount);
    priceAmount = Number.isFinite(n) ? n : null;
  }
  return {
    handle: b.handle,
    title: b.title,
    sku: b.sku,
    category: b.category,
    priceAmount,
    currency: b.currency,
    thumbnail: b.thumbnail,
  };
}

/** Snapshot del catálogo — fetch lazy (`enabled`) solo cuando el objetivo de
 *  la campaña necesita un producto. */
export function useProducts(enabled = true) {
  return useQuery<CatalogProduct[]>({
    queryKey: productKeys.list(),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>("/api/marketing/products", {
        signal,
      });
      return backendProductsResponseSchema
        .parse(raw)
        .products.map(mapBackendProduct);
    },
    staleTime: 5 * 60_000,
    enabled,
  });
}
