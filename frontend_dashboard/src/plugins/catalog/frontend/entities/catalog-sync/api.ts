/**
 * Hooks de `catalog-sync`. Consumen el backend real (`/api/catalog/*`) que
 * dispara + observa el `CatalogSyncWorkflow` (Medusa → copia local → Meta).
 *
 * Resiliencia (mismo patrón que `entities/order`): si el backend no responde
 * (`ApiError` o `TypeError` de fetch), el historial degrada a un shape vacío
 * con `available=false` para que la UI muestre un estado vacío explícito en
 * lugar de un error genérico.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef } from "react";
import {
  apiClient,
  ApiError,
  useDashboardEvents,
  useInvalidateOnReconnect,
} from "@/shared/api";
import {
  snapshotInfoSchema,
  syncDetailSchema,
  syncHistoryResponseSchema,
  triggerSyncResponseSchema,
  type SnapshotInfo,
  type SyncDetail,
  type SyncHistoryResponse,
  type TriggerSyncResponse,
} from "./contracts";
import { catalogSyncKeys } from "./keys";

// Fallback, NO el mecanismo primario: history/snapshot se refrescan por el
// evento `catalog.changed` (trigger del sync) + la invalidación local cuando
// el run activo termina (ver useSyncStatus). Antes: poll duro 5s/15s (F1).
const CATALOG_FALLBACK_REFETCH_MS = 5 * 60_000;

/**
 * Suscripción del plugin catalog al stream del dashboard (montar UNA vez en
 * CatalogSyncSection).
 */
export function useCatalogEvents() {
  const qc = useQueryClient();
  const invalidate = useCallback(
    () => qc.invalidateQueries({ queryKey: catalogSyncKeys.all }),
    [qc],
  );
  useDashboardEvents("catalog", invalidate);
  useInvalidateOnReconnect(invalidate);
}

const EMPTY_HISTORY: SyncHistoryResponse = {
  syncs: [],
  available: false,
  error_detail: "backend_unreachable",
};

function isBackendDown(exc: unknown): boolean {
  return (
    exc instanceof ApiError ||
    (exc instanceof TypeError && /fetch/i.test(exc.message))
  );
}

/* ── Historial (panel izquierdo) ───────────────────────────────────────── */

export function useSyncHistory() {
  return useQuery<SyncHistoryResponse>({
    queryKey: catalogSyncKeys.history(),
    queryFn: async ({ signal }) => {
      try {
        const raw = await apiClient.get<unknown>("/api/catalog/syncs", {
          signal,
        });
        return syncHistoryResponseSchema.parse(raw);
      } catch (exc) {
        if (isBackendDown(exc)) return EMPTY_HISTORY;
        throw exc; // Zod parse error u otro — propagar para visibilidad.
      }
    },
    refetchInterval: CATALOG_FALLBACK_REFETCH_MS,
    staleTime: 2_000,
    retry: 1,
  });
}

/* ── Estado de la copia local (snapshot) ───────────────────────────────── */

export function useSnapshotInfo() {
  return useQuery<SnapshotInfo>({
    queryKey: catalogSyncKeys.snapshot(),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>("/api/catalog/snapshot", {
        signal,
      });
      return snapshotInfoSchema.parse(raw);
    },
    refetchInterval: CATALOG_FALLBACK_REFETCH_MS,
    staleTime: 10_000,
    retry: 1,
  });
}

/* ── Estado + step-by-step de UN run ───────────────────────────────────── */

export function useSyncStatus(workflowId: string | null) {
  const qc = useQueryClient();
  const query = useQuery<SyncDetail>({
    queryKey: catalogSyncKeys.status(workflowId ?? "—"),
    queryFn: async ({ signal }) => {
      if (!workflowId) throw new Error("workflowId required");
      const raw = await apiClient.get<unknown>(
        `/api/catalog/sync/${encodeURIComponent(workflowId)}`,
        { signal },
      );
      return syncDetailSchema.parse(raw);
    },
    enabled: workflowId !== null && workflowId !== "",
    // Poll rápido mientras corre; se detiene solo cuando el run termina.
    // Este es el ÚNICO poll corto permitido (function-form acotado a un run
    // activo) — la finalización del workflow ocurre en el worker Temporal y
    // ningún evento del API la reporta.
    refetchInterval: (q) => (q.state.data?.status === "running" ? 1_200 : false),
    staleTime: 0,
    retry: 1,
  });

  // running → terminal: el sync que estábamos mirando terminó — history y
  // snapshot quedaron viejos y no hay evento server-side que lo avise (ver
  // comentario de arriba). Invalidamos acá, en el único lugar que LO VE.
  const status = query.data?.status;
  const prevStatus = useRef(status);
  useEffect(() => {
    if (prevStatus.current === "running" && status && status !== "running") {
      qc.invalidateQueries({ queryKey: catalogSyncKeys.history() });
      qc.invalidateQueries({ queryKey: catalogSyncKeys.snapshot() });
    }
    prevStatus.current = status;
  }, [status, qc]);

  return query;
}

/* ── Disparar un sync (botón Sincronizar) ──────────────────────────────── */

export function useTriggerSync() {
  const qc = useQueryClient();
  return useMutation<TriggerSyncResponse, Error, void>({
    mutationFn: async () => {
      const raw = await apiClient.post<unknown>("/api/catalog/sync", {});
      return triggerSyncResponseSchema.parse(raw);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: catalogSyncKeys.history() });
      qc.invalidateQueries({ queryKey: catalogSyncKeys.snapshot() });
    },
  });
}
