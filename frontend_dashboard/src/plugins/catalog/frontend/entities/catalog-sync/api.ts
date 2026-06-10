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
import { apiClient, ApiError } from "@/shared/api";
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
    queryFn: async () => {
      try {
        const raw = await apiClient.get<unknown>("/api/catalog/syncs");
        return syncHistoryResponseSchema.parse(raw);
      } catch (exc) {
        if (isBackendDown(exc)) return EMPTY_HISTORY;
        throw exc; // Zod parse error u otro — propagar para visibilidad.
      }
    },
    refetchInterval: 5_000,
    staleTime: 2_000,
    retry: 1,
  });
}

/* ── Estado de la copia local (snapshot) ───────────────────────────────── */

export function useSnapshotInfo() {
  return useQuery<SnapshotInfo>({
    queryKey: catalogSyncKeys.snapshot(),
    queryFn: async () => {
      const raw = await apiClient.get<unknown>("/api/catalog/snapshot");
      return snapshotInfoSchema.parse(raw);
    },
    refetchInterval: 15_000,
    staleTime: 10_000,
    retry: 1,
  });
}

/* ── Estado + step-by-step de UN run ───────────────────────────────────── */

export function useSyncStatus(workflowId: string | null) {
  return useQuery<SyncDetail>({
    queryKey: catalogSyncKeys.status(workflowId ?? "—"),
    queryFn: async () => {
      if (!workflowId) throw new Error("workflowId required");
      const raw = await apiClient.get<unknown>(
        `/api/catalog/sync/${encodeURIComponent(workflowId)}`,
      );
      return syncDetailSchema.parse(raw);
    },
    enabled: workflowId !== null && workflowId !== "",
    // Poll rápido mientras corre; se detiene solo cuando el run termina.
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 1_200 : false,
    staleTime: 0,
    retry: 1,
  });
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
