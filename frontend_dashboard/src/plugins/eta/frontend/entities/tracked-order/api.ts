import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { apiClient } from "@/shared/api/client";
import {
  useDashboardEvents,
  useInvalidateOnReconnect,
} from "@/shared/api/events";
import { trackedOrderKeys } from "./keys";
import { trackedOrdersListResponseSchema } from "./contracts";
import type { TrackedOrder } from "./model";

// Fallback, NO el mecanismo primario: la lista se refresca por eventos
// `eta.changed` (escrituras de eta_tracking en el vault, vía sampler) y
// `orders.changed` (los movimientos del kanban gatillan notificaciones ETA).
// Antes: poll duro cada 5s (auditoría F1).
const TRACKED_ORDERS_FALLBACK_REFETCH_MS = 5 * 60_000;

/**
 * Pedidos en seguimiento por el Agente ETA. Datos REALES: el backend
 * (`/api/eta/tracked-orders`) compone el listado desde
 * `metadata.eta_tracking` (timeline de notificaciones + respuestas) + el order
 * query port (cliente, ciudad, total, tipo de pago, stage actual).
 *
 * Antes esta entity devolvía un array `TRACKED` mockeado; ahora hace fetch real
 * + validación Zod en el boundary y poll cada 5s para reflejar nuevos cambios
 * de estado a medida que el operador mueve los pedidos en el kanban de orders.
 */
async function fetchTrackedOrders(
  signal?: AbortSignal,
): Promise<TrackedOrder[]> {
  const raw = await apiClient.get<unknown>("/api/eta/tracked-orders", {
    signal,
  });
  return trackedOrdersListResponseSchema.parse(raw).orders as TrackedOrder[];
}

export function useTrackedOrders() {
  return useQuery({
    queryKey: trackedOrderKeys.list(),
    queryFn: ({ signal }) => fetchTrackedOrders(signal),
    refetchInterval: TRACKED_ORDERS_FALLBACK_REFETCH_MS,
  });
}

/**
 * Suscripción del plugin eta al stream del dashboard (montar UNA vez en
 * EtaSection). Invalida la lista ante `eta.changed` Y `orders.changed`: el
 * timeline ETA deriva de ambos (tracking del vault + stage actual del kanban).
 */
export function useTrackedOrdersEvents() {
  const qc = useQueryClient();
  const invalidate = useCallback(
    () => qc.invalidateQueries({ queryKey: trackedOrderKeys.all }),
    [qc],
  );
  useDashboardEvents("eta", invalidate);
  useDashboardEvents("orders", invalidate);
  useInvalidateOnReconnect(invalidate);
}
