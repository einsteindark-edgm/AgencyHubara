/**
 * Mutations del canvas de pago — SIEMPRE contra el API PROPIO de chats
 * (`/api/chats/order-actions/*`, el cast declarado en el manifest), nunca
 * contra `/api/orders/*` (P-23: los literales /api del código de chats deben
 * pertenecer a chats).
 *
 * Nota de cache: NO invalidamos las query keys del plugin orders (sería
 * acoplamiento de cache cross-plugin). El tablero de orders se refresca por
 * sus propios medios (refetch on focus / su propio ciclo); la UI de chats
 * solo necesita `sessionKeys` (lo invalida el caller, ConfirmPaymentAction).
 */
import { useMutation } from "@tanstack/react-query";

import { apiClient } from "@/shared/api/client";

import {
  orderRefCommandResultSchema,
  type OrderRefCommandResult,
} from "./contracts";

interface ScheduleOrderVariables {
  orderId: string;
  delivery_iso: string;
  delivery_time?: string;
  note?: string;
}

export function useScheduleOrder() {
  return useMutation<OrderRefCommandResult, Error, ScheduleOrderVariables>({
    mutationFn: async ({ orderId, ...body }) => {
      const raw = await apiClient.patch<unknown>(
        `/api/chats/order-actions/${encodeURIComponent(orderId)}/schedule`,
        body,
      );
      return orderRefCommandResultSchema.parse(raw);
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
