/**
 * Mutations del canvas de pago — SIEMPRE contra el API PROPIO de chats
 * (`/api/chats/order-actions/*`, el cast declarado en el manifest), nunca
 * contra el API del plugin orders (P-9/P-23: los literales /api del código
 * de chats deben pertenecer a chats).
 *
 * Nota de cache: NO invalidamos las query keys del plugin orders (sería
 * acoplamiento de cache cross-plugin). El tablero de orders se refresca por
 * sus propios medios (refetch on focus / su propio ciclo); la UI de chats
 * solo necesita `sessionKeys` (lo invalida el caller, ConfirmPaymentAction)
 * y las keys PROPIAS de esta entity (`orderRefKeys.detail` — agendar cambia
 * `due_iso`, y "Confirmar pago" decide con ese campo si re-agendar o no).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api/client";

import {
  customerOrdersSchema,
  orderRefCommandResultSchema,
  orderRefDetailSchema,
  type CustomerOrders,
  type OrderRefCommandResult,
  type OrderRefDetail,
  type OrderRefStatus,
} from "./contracts";
import { orderRefKeys } from "./keys";

/**
 * Pedidos DE ESTE cliente (panel del chat móvil) vía el cast
 * (`GET /api/chats/order-actions/by-session/{sessionId}`). El backend resuelve
 * el vínculo sesión→órdenes desde el vault. `enabled` = lazy (al abrir el panel).
 */
export function useCustomerOrders(
  sessionId: string | null,
  opts?: { enabled?: boolean },
) {
  return useQuery<CustomerOrders, Error>({
    queryKey: orderRefKeys.bySession(sessionId ?? "none"),
    enabled: Boolean(sessionId) && (opts?.enabled ?? true),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>(
        `/api/chats/order-actions/by-session/${encodeURIComponent(sessionId ?? "")}`,
        { signal },
      );
      return customerOrdersSchema.parse(raw);
    },
  });
}

interface TransitionStageVariables {
  orderId: string;
  stage: OrderRefStatus;
  note?: string;
  force?: boolean;
}

/**
 * Cambia el estado de un pedido manualmente (`PATCH .../{id}/stage`). El
 * backend valida la transición contra el DAG; si es inválida devuelve
 * `success:false` con `error_detail`. Invalida el listado de la sesión para
 * repintar el estado nuevo. `sessionId` = la sesión del panel abierto.
 */
export function useTransitionOrderStage(sessionId: string | null) {
  const qc = useQueryClient();
  return useMutation<OrderRefCommandResult, Error, TransitionStageVariables>({
    mutationFn: async ({ orderId, ...body }) => {
      const raw = await apiClient.patch<unknown>(
        `/api/chats/order-actions/${encodeURIComponent(orderId)}/stage`,
        body,
      );
      return orderRefCommandResultSchema.parse(raw);
    },
    onSuccess: () => {
      if (sessionId) {
        qc.invalidateQueries({ queryKey: orderRefKeys.bySession(sessionId) });
      }
    },
  });
}

/**
 * Detalle del pedido vía el read-side del cast (`GET /order-actions/{id}`).
 * El canvas de pago lo usa para saber si la entrega YA está agendada
 * (`summary.due_iso`) y así no re-agendar al confirmar el pago.
 *
 * `opts.enabled` permite fetch LAZY (al abrir el popover, no al montar el
 * composer — PM-006): el read del cast va a Medusa live y el composer se
 * monta en cada chat intervenido con pago pendiente.
 */
export function useOrderRefDetail(
  orderId: string | null,
  opts?: { enabled?: boolean },
) {
  return useQuery<OrderRefDetail, Error>({
    queryKey: orderRefKeys.detail(orderId ?? "none"),
    enabled: Boolean(orderId) && (opts?.enabled ?? true),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>(
        `/api/chats/order-actions/${encodeURIComponent(orderId ?? "")}`,
        { signal },
      );
      return orderRefDetailSchema.parse(raw);
    },
  });
}

interface ScheduleOrderVariables {
  orderId: string;
  delivery_iso: string;
  delivery_time?: string;
  note?: string;
}

export function useScheduleOrder() {
  const qc = useQueryClient();
  return useMutation<OrderRefCommandResult, Error, ScheduleOrderVariables>({
    mutationFn: async ({ orderId, ...body }) => {
      const raw = await apiClient.patch<unknown>(
        `/api/chats/order-actions/${encodeURIComponent(orderId)}/schedule`,
        body,
      );
      return orderRefCommandResultSchema.parse(raw);
    },
    onSuccess: (_data, { orderId }) => {
      qc.invalidateQueries({ queryKey: orderRefKeys.detail(orderId) });
    },
  });
}

interface ConfirmPaymentVariables {
  orderId: string;
}

export function useConfirmOrderPayment() {
  return useMutation<OrderRefCommandResult, Error, ConfirmPaymentVariables>({
    mutationFn: async ({ orderId }) => {
      const raw = await apiClient.patch<unknown>(
        `/api/chats/order-actions/${encodeURIComponent(orderId)}/confirm-payment`,
        {},
      );
      return orderRefCommandResultSchema.parse(raw);
    },
  });
}
