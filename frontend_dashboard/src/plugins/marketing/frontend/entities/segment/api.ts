/**
 * Hook de fetching de segmentos de audiencia (`/api/marketing/segments`).
 * Zod en el boundary + mapper snake→camel.
 */

import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/sdk";

import {
  backendSegmentsResponseSchema,
  type BackendSegmentsResponse,
} from "./contracts";
import { segmentKeys } from "./keys";
import type { SegmentsInfo } from "./model";

export function mapBackendSegments(b: BackendSegmentsResponse): SegmentsInfo {
  return {
    segments: b.segments.map((s) => ({
      key: s.key,
      label: s.label,
      description: s.description,
      count: s.count,
    })),
    excludedCount: b.excluded_count,
    unitCostUsdMicros: b.unit_cost_usd_micros,
    currency: b.currency,
  };
}

/** Conteos por segmento + costo unitario. El backend escanea el vault en
 *  cada call — staleTime moderado para no martillarlo desde el builder. */
export function useSegmentsInfo() {
  return useQuery<SegmentsInfo>({
    queryKey: segmentKeys.info(),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>("/api/marketing/segments", {
        signal,
      });
      return mapBackendSegments(backendSegmentsResponseSchema.parse(raw));
    },
    staleTime: 60_000,
  });
}
