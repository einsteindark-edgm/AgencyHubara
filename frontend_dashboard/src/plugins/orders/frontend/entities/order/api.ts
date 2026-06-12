/**
 * Hooks de orders. Consume el backend real (`/api/orders/orders` y
 * `/api/orders/orders/{id}`) que sirve Medusa v2 vía `MedusaOrderQuery`.
 *
 * Mapping: el backend devuelve shape Zod-validated (`OrderSummary` con
 * snake_case + nuevos campos como `is_draft`, `due_iso`, etc.). El
 * `Order` interface histórico (camelCase) sigue siendo el contrato que
 * consumen `OrdersBoard`, `OrdersFilters`, `OrdersInspector` — el mapper
 * `toLegacyOrder` traduce.
 *
 * Premortem fixes:
 *  * **C3**: si el backend mismo NO responde (no Medusa, BACKEND) capturamos
 *    `ApiError` y devolvemos el shape vacío + `catalog_available: false` —
 *    así la UI muestra el mismo banner que cuando Medusa cae.
 *  * **A1**: el detail endpoint acepta display_id ("#1247"), así que ya NO
 *    necesitamos un `_idByDisplay` Map global frágil. Pasamos el display_id
 *    directo en la URL.
 *  * **F2+K1**: `useVaultOrders` lista pedidos rechazados/stub para que el
 *    operador pueda reconciliarlos manualmente.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import {
  apiClient,
  ApiError,
  useDashboardEvents,
  useInvalidateOnReconnect,
} from "@/shared/api";
import {
  customerScoreSchema,
  customerSummarySchema,
  orderCommandResultSchema,
  orderDetailSchema,
  orderListResponseSchema,
  reconciliationOutcomeSchema,
  vaultOrdersResponseSchema,
  type CustomerScore,
  type CustomerSummary,
  type OrderCommandResult,
  type OrderDetail,
  type OrderListResponse,
  type OrderSummary,
  type ReconciliationOutcome,
  type VaultOrdersResponse,
} from "./contracts";
import { orderKeys } from "./keys";
import type { Order, OrderStatus, PayStatus, PayType } from "./model";

/* ── Mapping snake_case backend → camelCase legacy Order interface ─────── */

// Mapper PURO (auditoría 2026-06-10, F0.5): nada de `new Date()` acá — un
// dato cacheado no puede depender del reloj del momento del fetch. `overdue`
// viene calculado del backend (única fuente); los labels relativos
// ("hoy/mañana") se derivan en render (ver `dayChipShort` en OrdersBoard).
export function toLegacyOrder(s: OrderSummary): Order {
  return {
    id: s.display_id, // "#1247" — la UI espera el formato display
    customer: s.customer,
    short: s.short,
    color: s.color,
    phone: s.phone ?? "—",
    city: s.city ?? "—",
    channel: s.channel,
    status: s.status as OrderStatus,
    payStatus: s.pay_status as PayStatus,
    payType: s.pay_type as PayType,
    items: s.items,
    total: s.total_cop,
    dueIso: s.due_iso ?? "",
    dueTime: s.due_time ?? "—",
    overdue: s.overdue,
    pieces: s.pieces,
    agent: s.agent,
    priority: s.priority,
    isDraft: s.is_draft,
    // El `due_iso` viene del backend como estimate (created+1d) — siempre
    // estimated hasta que tengamos integración real de shipping.
    isDueEstimated: s.due_iso !== null,
  };
}

/* ── Queries ──────────────────────────────────────────────────────────── */

// Fallback, NO el mecanismo primario: las órdenes se refrescan por el evento
// `orders.changed` (mutaciones del API + register_order del agente vía el
// sampler del vault). Antes: poll duro 30s lista + 60s vault (auditoría F1).
const ORDERS_FALLBACK_REFETCH_MS = 5 * 60_000;

/**
 * Suscripción del plugin orders al stream del dashboard. Montar UNA vez por
 * sección (OrdersSection): cualquier `orders.changed` invalida el dominio
 * entero (lista + vault + detalle + score comparten el prefijo `orderKeys.all`
 * — el server es la única fuente; refetch targeted llega con keys por id en
 * el evento si algún día hace falta granularidad).
 */
export function useOrdersEvents() {
  const qc = useQueryClient();
  const invalidate = useCallback(
    () => qc.invalidateQueries({ queryKey: orderKeys.all }),
    [qc],
  );
  useDashboardEvents("orders", invalidate);
  useInvalidateOnReconnect(invalidate);
}

export function useOrders() {
  return useQuery<{ orders: Order[]; response: OrderListResponse }>({
    queryKey: orderKeys.list(),
    queryFn: async ({ signal }) => {
      try {
        const raw = await apiClient.get<unknown>("/api/orders/orders", {
          signal,
        });
        const parsed = orderListResponseSchema.parse(raw);
        return {
          orders: parsed.orders.map(toLegacyOrder),
          response: parsed,
        };
      } catch (exc) {
        // Premortem C3: si el BACKEND mismo no responde (no Medusa,
        // backend), generamos un shape válido con catalog_available=false
        // para que la UI muestre el banner explícito en lugar de un
        // error genérico "Failed to fetch".
        if (exc instanceof ApiError) {
          const empty: OrderListResponse = {
            orders: [],
            count: 0,
            offset: 0,
            limit: 100,
            catalog_available: false,
            error_detail: `backend_api_error: HTTP ${exc.status}`,
          };
          return { orders: [], response: empty };
        }
        // Network failure (fetch threw before HTTP): mismo trato.
        if (exc instanceof TypeError && /fetch/i.test(exc.message)) {
          const empty: OrderListResponse = {
            orders: [],
            count: 0,
            offset: 0,
            limit: 100,
            catalog_available: false,
            error_detail: "backend_unreachable: el backend no responde",
          };
          return { orders: [], response: empty };
        }
        throw exc; // Zod parse error u otro — propagar para visibilidad.
      }
    },
    // Primario: evento `orders.changed` (useOrdersEvents). Esto es solo
    // la red de seguridad si el stream está caído.
    refetchInterval: ORDERS_FALLBACK_REFETCH_MS,
    staleTime: 10_000,
    retry: 1, // Premortem C3: 1 retry rápido, luego fallback a empty.
  });
}

export function useOrderDetail(displayId: string | null) {
  return useQuery<OrderDetail>({
    queryKey: orderKeys.detail(displayId ?? "—"),
    queryFn: async ({ signal }) => {
      if (!displayId) throw new Error("displayId required");
      // Premortem A1: el backend ya resuelve `display_id` ("#1247") al
      // backend id internamente. Pasamos el display_id directo —
      // funciona para deep-links sin requerir useOrders() poblado antes.
      const raw = await apiClient.get<unknown>(
        `/api/orders/orders/${encodeURIComponent(displayId)}`,
        { signal },
      );
      return orderDetailSchema.parse(raw);
    },
    enabled: displayId !== null && displayId !== "",
    staleTime: 10_000,
  });
}

/* ── Write-side mutations (F10) ────────────────────────────────────────
 *
 * 4 endpoints PATCH/POST que el dashboard usa para mutar órdenes:
 *   useScheduleOrder       → PATCH /api/orders/orders/{id}/schedule
 *   useTransitionOrderStage→ PATCH /api/orders/orders/{id}/stage
 *   useConfirmOrderPayment → PATCH /api/orders/orders/{id}/confirm-payment
 *   useCancelOrder         → POST  /api/orders/orders/{id}/cancel
 *
 * Todas devuelven `OrderCommandResult` (success/error_detail) y todas
 * invalidate `orderKeys.list()` + `orderKeys.detail(id)` al succeed.
 * El componente que llama maneja el dialog de error_detail si success=false.
 */

interface ScheduleOrderVariables {
  orderId: string;
  delivery_iso: string;
  delivery_time?: string;
  note?: string;
}

export function useScheduleOrder() {
  const qc = useQueryClient();
  return useMutation<OrderCommandResult, Error, ScheduleOrderVariables>({
    mutationFn: async ({ orderId, ...body }) => {
      const raw = await apiClient.patch<unknown>(
        `/api/orders/orders/${encodeURIComponent(orderId)}/schedule`,
        body,
      );
      return orderCommandResultSchema.parse(raw);
    },
    onSuccess: (result, vars) => {
      if (result.success) {
        qc.invalidateQueries({ queryKey: orderKeys.list() });
        qc.invalidateQueries({ queryKey: orderKeys.detail(vars.orderId) });
      }
    },
  });
}

interface TransitionStageVariables {
  orderId: string;
  to_stage: OrderStatus;
  note?: string;
  force?: boolean;
}

export function useTransitionOrderStage() {
  const qc = useQueryClient();
  return useMutation<OrderCommandResult, Error, TransitionStageVariables>({
    mutationFn: async ({ orderId, to_stage, note, force }) => {
      const raw = await apiClient.patch<unknown>(
        `/api/orders/orders/${encodeURIComponent(orderId)}/stage`,
        { stage: to_stage, note, force: force ?? false },
      );
      return orderCommandResultSchema.parse(raw);
    },
    onMutate: async ({ orderId, to_stage }) => {
      // Optimistic update del kanban: movemos la card antes de la respuesta.
      // Si el server rechaza (success=false), `onError` hace rollback.
      await qc.cancelQueries({ queryKey: orderKeys.list() });
      const previous = qc.getQueryData<{
        orders: Order[];
        response: OrderListResponse;
      }>(orderKeys.list());
      if (previous) {
        qc.setQueryData(orderKeys.list(), {
          ...previous,
          orders: previous.orders.map((o) =>
            o.id === orderId ? { ...o, status: to_stage } : o,
          ),
        });
      }
      return { previous };
    },
    onError: (_err, _vars, context) => {
      const ctx = context as { previous?: unknown } | undefined;
      if (ctx?.previous) {
        qc.setQueryData(orderKeys.list(), ctx.previous);
      }
    },
    onSettled: (_data, _err, vars) => {
      // Siempre refetch para reconciliar con server state.
      qc.invalidateQueries({ queryKey: orderKeys.list() });
      qc.invalidateQueries({ queryKey: orderKeys.detail(vars.orderId) });
    },
  });
}

interface ConfirmPaymentVariables {
  orderId: string;
}

export function useConfirmOrderPayment() {
  const qc = useQueryClient();
  return useMutation<OrderCommandResult, Error, ConfirmPaymentVariables>({
    mutationFn: async ({ orderId }) => {
      const raw = await apiClient.patch<unknown>(
        `/api/orders/orders/${encodeURIComponent(orderId)}/confirm-payment`,
        {},
      );
      return orderCommandResultSchema.parse(raw);
    },
    onSuccess: (result, vars) => {
      if (result.success) {
        qc.invalidateQueries({ queryKey: orderKeys.list() });
        qc.invalidateQueries({ queryKey: orderKeys.detail(vars.orderId) });
      }
    },
  });
}

interface CancelOrderVariables {
  orderId: string;
  reason?: string;
}

export function useCancelOrder() {
  const qc = useQueryClient();
  return useMutation<OrderCommandResult, Error, CancelOrderVariables>({
    mutationFn: async ({ orderId, reason }) => {
      const raw = await apiClient.post<unknown>(
        `/api/orders/orders/${encodeURIComponent(orderId)}/cancel`,
        { reason },
      );
      return orderCommandResultSchema.parse(raw);
    },
    onSuccess: (result, vars) => {
      if (result.success) {
        qc.invalidateQueries({ queryKey: orderKeys.list() });
        qc.invalidateQueries({ queryKey: orderKeys.detail(vars.orderId) });
      }
    },
  });
}

export function useVaultOrders() {
  return useQuery<VaultOrdersResponse>({
    queryKey: orderKeys.vault(),
    queryFn: async ({ signal }) => {
      try {
        const raw = await apiClient.get<unknown>("/api/orders/vault-orders", {
          signal,
        });
        return vaultOrdersResponseSchema.parse(raw);
      } catch (exc) {
        // Mismo trato C3: si el backend está caído, devolvemos vacío para
        // no romper la UI principal del kanban.
        if (exc instanceof ApiError || (exc instanceof TypeError && /fetch/i.test(exc.message))) {
          return { records: [], count: 0, failed_count: 0, stub_count: 0 };
        }
        throw exc;
      }
    },
    // Primario: evento `orders.changed` (las acciones del banner publican).
    refetchInterval: ORDERS_FALLBACK_REFETCH_MS,
    staleTime: 30_000,
    retry: 1,
  });
}

/* ── Reconciliación — acciones del banner de pedidos pendientes ────────── */

interface RetryVaultOrderVariables {
  sessionKey: string;
  auditId: string;
}

/**
 * Reintenta registrar en Medusa un pedido pendiente ("Reintentar").
 * Idempotente — usa el MISMO núcleo que el barrido automático del backend.
 * Invalida el banner: si se resolvió, el record desaparece.
 */
export function useRetryVaultOrder() {
  const qc = useQueryClient();
  return useMutation<ReconciliationOutcome, Error, RetryVaultOrderVariables>({
    mutationFn: async ({ sessionKey, auditId }) => {
      const raw = await apiClient.post<unknown>(
        `/api/orders/vault-orders/${encodeURIComponent(sessionKey)}/${encodeURIComponent(auditId)}/retry`,
      );
      return reconciliationOutcomeSchema.parse(raw);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: orderKeys.vault() });
      qc.invalidateQueries({ queryKey: orderKeys.list() });
    },
  });
}

interface ResolveVaultOrderVariables {
  sessionKey: string;
  auditId: string;
  note?: string;
  resolvedOrderId?: string;
}

/**
 * Marca un pedido pendiente como resuelto a mano ("Marcar resuelto"). NO toca
 * Medusa — el operador ya lo registró en Medusa Admin manualmente.
 */
export function useResolveVaultOrder() {
  const qc = useQueryClient();
  return useMutation<ReconciliationOutcome, Error, ResolveVaultOrderVariables>({
    mutationFn: async ({ sessionKey, auditId, note, resolvedOrderId }) => {
      const raw = await apiClient.post<unknown>(
        `/api/orders/vault-orders/${encodeURIComponent(sessionKey)}/${encodeURIComponent(auditId)}/resolve`,
        { note, resolved_order_id: resolvedOrderId },
      );
      return reconciliationOutcomeSchema.parse(raw);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: orderKeys.vault() });
    },
  });
}

/* ── Customer Score — panel "Historial cliente" del inspector ────────────
 *
 * GET /api/orders/orders/{id}/customer-score — derivado del vault metadata
 * + Medusa orders cruzados con el rules.yaml del backend. Determinístico:
 * el mismo cliente siempre da el mismo score (modulo cambios en rules.yaml,
 * que el backend detecta por mtime y recarga sin redeploy).
 *
 * Si la order no tiene phone o el vault no tiene historial, devuelve
 * tag="Sin datos" + letter="—" — el componente renderea MissingData en
 * ese caso.
 */

export function useCustomerScore(displayId: string | null) {
  return useQuery<CustomerScore>({
    queryKey: orderKeys.customerScore(displayId ?? "—"),
    queryFn: async ({ signal }) => {
      if (!displayId) throw new Error("displayId required");
      const raw = await apiClient.get<unknown>(
        `/api/orders/orders/${encodeURIComponent(displayId)}/customer-score`,
        { signal },
      );
      return customerScoreSchema.parse(raw);
    },
    enabled: displayId !== null && displayId !== "",
    // El score cambia con baja frecuencia (depende de episodes nuevos +
    // rules.yaml). 5min staleTime es razonable.
    staleTime: 5 * 60 * 1000,
  });
}

/* ── Customer Summary — botón "Resumir con IA" on-demand ─────────────────
 *
 * POST /api/orders/orders/{id}/customer-summary — invoca al LLM. Mutation
 * (no query) porque tiene side-effect (costo de tokens) y el operador la
 * dispara explícitamente. No cacheamos — siempre fresh.
 */

interface GenerateCustomerSummaryVariables {
  orderId: string;
}

export function useGenerateCustomerSummary() {
  return useMutation<CustomerSummary, Error, GenerateCustomerSummaryVariables>({
    mutationFn: async ({ orderId }) => {
      const raw = await apiClient.post<unknown>(
        `/api/orders/orders/${encodeURIComponent(orderId)}/customer-summary`,
        {},
      );
      return customerSummarySchema.parse(raw);
    },
  });
}
