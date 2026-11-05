/**
 * Boundary HTTP de Costos: SOLO `/api/costs/*`, con el `apiClient` del SDK,
 * `{signal}` en cada query y Zod `.parse()` antes de que el dato siga.
 *
 * Una query POR proveedor: cada tarjeta carga (o falla) sola, así un
 * proveedor lento no frena la pestaña. Sin `refetchInterval`: los costos no
 * cambian minuto a minuto y el backend ya guarda en caché; «Actualizar» pide
 * con `refresh=true` y escribe la respuesta en la caché de esa tarjeta.
 */

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/sdk";

import {
  backendCatalogSchema,
  backendProviderReportSchema,
  type BackendCatalog,
  type BackendProviderReport,
} from "./contracts";
import { costKeys } from "./keys";
import type { CostCatalog, CostRange, ProviderReport } from "./model";

const BASE = "/api/costs";

export function mapBackendCatalog(b: BackendCatalog): CostCatalog {
  return {
    providers: b.providers.map((p) => ({ id: p.id, label: p.label, connected: p.connected })),
    today: b.today,
    defaultStart: b.default_start,
    defaultEnd: b.default_end,
    maxDays: b.max_days,
  };
}

export function mapBackendReport(b: BackendProviderReport): ProviderReport {
  return {
    provider: b.provider,
    status: b.status,
    source: b.source,
    start: b.start,
    end: b.end,
    productionUsdMicros: b.production_usd_micros,
    productionCopMicros: b.production_cop_micros,
    otherUsdMicros: b.other_usd_micros,
    daily: b.daily_production_usd_micros.map((d) => ({ date: d.date, usdMicros: d.usd_micros })),
    lines: b.lines.map((l) => ({
      key: l.key,
      label: l.label,
      group: l.group,
      currency: l.currency,
      amountMicros: l.amount_micros,
      usdMicros: l.usd_micros,
      copMicros: l.cop_micros,
      production: l.production,
      quantity: l.quantity,
      unit: l.unit,
    })),
    notes: [...b.notes],
    warnings: [...b.warnings],
    error: b.error,
    howToConnect: b.how_to_connect,
    fxNote: b.fx_note,
    fetchedAt: b.fetched_at,
    cached: b.cached,
  };
}

export function reportPath(providerId: string, range: CostRange, refresh = false): string {
  const qs = new URLSearchParams({ start: range.start, end: range.end });
  if (refresh) qs.set("refresh", "true");
  return `${BASE}/providers/${encodeURIComponent(providerId)}?${qs.toString()}`;
}

/** Las tarjetas, si cada una está conectada y el rango por defecto (2 semanas). */
export function useCostCatalog() {
  return useQuery<CostCatalog>({
    queryKey: costKeys.catalog(),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>(`${BASE}/providers`, { signal });
      return mapBackendCatalog(backendCatalogSchema.parse(raw));
    },
    staleTime: 60_000,
  });
}

async function fetchReport(
  providerId: string,
  range: CostRange,
  signal?: AbortSignal,
  refresh = false,
): Promise<ProviderReport> {
  const raw = await apiClient.get<unknown>(reportPath(providerId, range, refresh), { signal });
  return mapBackendReport(backendProviderReportSchema.parse(raw));
}

/** Un informe por proveedor, en paralelo; `range` null = todavía no hay rango. */
export function useProviderReports(providerIds: string[], range: CostRange | null) {
  return useQueries({
    queries: providerIds.map((id) => ({
      queryKey: range ? costKeys.report(id, range) : [...costKeys.reports(), id, "sin-rango"],
      queryFn: ({ signal }: { signal: AbortSignal }) => fetchReport(id, range as CostRange, signal),
      enabled: range !== null,
      staleTime: 5 * 60_000,
      retry: 1,
    })),
  });
}

/** «Actualizar» de una tarjeta: pregunta de nuevo al proveedor (sin caché). */
export function useRefreshProviderReport(providerId: string, range: CostRange | null) {
  const qc = useQueryClient();
  return useMutation<ProviderReport, Error, void>({
    mutationFn: () => fetchReport(providerId, range as CostRange, undefined, true),
    onSuccess: (report) => {
      if (range) qc.setQueryData(costKeys.report(providerId, range), report);
    },
  });
}
