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
  orderRefPhotoSchema,
  type CustomerOrders,
  type OrderRefCommandResult,
  type OrderRefDetail,
  type OrderRefPhoto,
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
    // PM2-M6: red de seguridad — el listado no tiene push SSE propio; si otro
    // dispositivo (desktop) cambia el estado con el sheet abierto, sin esto los
    // botones quedan stale indefinidamente. ≥60s numérico = permitido por la
    // política realtime (regla 2a). La query solo está montada con el sheet
    // abierto, así que el costo es acotado.
    refetchInterval: 60_000,
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
  /** "En camino": valor del envío en COP entero (el ETA lo detalla al cliente). */
  shipping_cost?: number;
  /** "En camino": link de la guía, ya normalizado a http(s). */
  tracking_url?: string;
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
    // PM2-M5: RETORNAR la promise del invalidate — así `mutateAsync` no
    // resuelve hasta que el refetch termina y la tarjeta ya pinta el estado
    // NUEVO cuando el botón se re-habilita (sin esto, con Medusa lento, el
    // botón volvía a decir "Despachar" habilitado durante segundos y el
    // re-tap daba "transición inválida"). PM2-M8: invalidar también el
    // detail — ConfirmPayment/ScheduleDelivery deciden con esa foto.
    onSuccess: (_data, { orderId }) =>
      Promise.all([
        sessionId
          ? qc.invalidateQueries({ queryKey: orderRefKeys.bySession(sessionId) })
          : Promise.resolve(),
        qc.invalidateQueries({ queryKey: orderRefKeys.detail(orderId) }),
      ]),
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

function photoPath(orderId: string): string {
  return `/api/chats/order-actions/${encodeURIComponent(orderId)}/photo`;
}

/**
 * Foto del pedido listo + canal por el que le llegaría al cliente (paso
 * "Marcar listo" del panel móvil). `enabled` = lazy (al abrir el paso).
 */
export function useOrderRefPhoto(
  orderId: string | null,
  opts?: { enabled?: boolean },
) {
  return useQuery<OrderRefPhoto, Error>({
    queryKey: orderRefKeys.photo(orderId ?? "none"),
    enabled: Boolean(orderId) && (opts?.enabled ?? true),
    queryFn: async ({ signal }) =>
      orderRefPhotoSchema.parse(
        await apiClient.get<unknown>(photoPath(orderId ?? ""), { signal }),
      ),
  });
}

/**
 * Sube (o reemplaza) la foto del pedido como multipart `file`. Al pasar el
 * pedido a "listo", el Agente ETA se la manda al cliente por WhatsApp.
 */
export function useUploadOrderRefPhoto() {
  const qc = useQueryClient();
  return useMutation<OrderRefPhoto, Error, { orderId: string; file: Blob }>({
    mutationFn: async ({ orderId, file }) => {
      const form = new FormData();
      form.append("file", file, "pedido.jpg");
      return orderRefPhotoSchema.parse(await apiClient.put<unknown>(photoPath(orderId), form));
    },
    onSuccess: (data, { orderId }) => qc.setQueryData(orderRefKeys.photo(orderId), data),
  });
}
