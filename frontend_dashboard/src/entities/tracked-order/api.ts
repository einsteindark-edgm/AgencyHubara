import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/shared/api/client";
import { trackedOrderKeys } from "./keys";
import { trackedOrdersListResponseSchema } from "./contracts";
import type { TrackedOrder } from "./model";

const TRACKED_ORDERS_REFETCH_MS = 5_000;

/**
 * Pedidos en seguimiento por el Agente ETA. Datos REALES: el backend
 * (`/api/chats/eta/tracked-orders`) compone el listado desde
 * `metadata.eta_tracking` (timeline de notificaciones + respuestas) + el order
 * query port (cliente, ciudad, total, tipo de pago, stage actual).
 *
 * Antes esta entity devolvía un array `TRACKED` mockeado; ahora hace fetch real
 * + validación Zod en el boundary y poll cada 5s para reflejar nuevos cambios
 * de estado a medida que el operador mueve los pedidos en el kanban de orders.
 */
async function fetchTrackedOrders(): Promise<TrackedOrder[]> {
  const raw = await apiClient.get<unknown>("/api/chats/eta/tracked-orders");
  return trackedOrdersListResponseSchema.parse(raw).orders as TrackedOrder[];
}

export function useTrackedOrders() {
  return useQuery({
    queryKey: trackedOrderKeys.list(),
    queryFn: fetchTrackedOrders,
    refetchInterval: TRACKED_ORDERS_REFETCH_MS,
  });
}
