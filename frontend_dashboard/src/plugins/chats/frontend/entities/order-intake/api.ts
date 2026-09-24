/**
 * Hooks del botón "Crear pedido" del chat intervenido.
 *
 * Dos MUTATIONS (no queries): ambas arrancan por un acto explícito del
 * operador. `suggest` además cuesta una llamada a DeepSeek — no es algo que
 * deba dispararse solo al montar el composer ni refrescarse en background.
 *
 * Ambas pegan contra `/api/chats/*` (endpoints PROPIOS de chats). El pedido se
 * registra por `session-actions@v1`, el MISMO contrato que usa Meta Business
 * Agent: un solo camino de escritura hacia Medusa, con su chequeo de montos
 * server-side y su idempotencia por contenido.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api/client";
import { sessionKeys } from "@plugins/chats/frontend/entities/session";

import {
  createOrderResultSchema,
  orderSuggestionSchema,
  type CreateOrderResult,
  type OrderSuggestion,
  type PaymentMethod,
} from "./contracts";

/**
 * Lee la conversación (DeepSeek) y devuelve el formulario pre-llenado.
 * READ-ONLY: no toca el vault, así que se puede reintentar sin miedo.
 */
export function useSuggestOrderFromChat(sessionId: string | null) {
  return useMutation<OrderSuggestion, Error, void>({
    mutationFn: async () => {
      const raw = await apiClient.post<unknown>(
        `/api/chats/order-intake/${encodeURIComponent(sessionId ?? "")}/suggest`,
      );
      return orderSuggestionSchema.parse(raw);
    },
  });
}

export interface CreateOrderVariables {
  /** `color`/`aroma`: valor de la lista del producto (cupo por unidad); se
   *  omiten cuando el producto no tiene ese atributo. */
  items: Array<{
    handle: string;
    variant_label?: string;
    quantity: number;
    color?: string;
    aroma?: string;
  }>;
  shipping: {
    city: string;
    neighborhood?: string;
    address: string;
    phone: string;
    receiver_name: string;
    national_id?: string;
  };
  payment_method: PaymentMethod;
  /** `false` = el operador ya acordó el pago por chat y no quiere el mensaje automático. */
  send_payment_instructions: boolean;
}

/**
 * Registra el pedido. Al volver, invalida `sessionKeys`: la sesión pasa a
 * exponer `pending_payment_order_id` y el composer pinta "Confirmar pago" /
 * "Asignar fecha" sin que el operador tenga que recargar.
 */
export function useCreateOrderFromChat(sessionId: string | null) {
  const qc = useQueryClient();
  return useMutation<CreateOrderResult, Error, CreateOrderVariables>({
    mutationFn: async (body) => {
      const raw = await apiClient.post<unknown>(
        `/api/chats/session-actions/${encodeURIComponent(sessionId ?? "")}/order`,
        body,
      );
      return createOrderResultSchema.parse(raw);
    },
    onSuccess: (result) =>
      result.registered
        ? qc.invalidateQueries({ queryKey: sessionKeys.all })
        : Promise.resolve(),
  });
}
