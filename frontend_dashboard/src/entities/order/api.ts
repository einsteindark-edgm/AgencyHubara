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

import { useQuery } from "@tanstack/react-query";
import { apiClient, ApiError } from "@/shared/api";
import {
  orderDetailSchema,
  orderListResponseSchema,
  vaultOrdersResponseSchema,
  type OrderDetail,
  type OrderListResponse,
  type OrderSummary,
  type VaultOrdersResponse,
} from "./contracts";
import { orderKeys } from "./keys";
import type { Order, OrderStatus, PayStatus, PayType } from "./model";

/* ── Mapping snake_case backend → camelCase legacy Order interface ─────── */

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
    due: humanizeDue(s.due_iso),
    dueIso: s.due_iso ?? "",
    dueTime: s.due_time ?? "—",
    overdue: computeOverdue(s.due_iso),
    pieces: s.pieces,
    agent: s.agent,
    priority: s.priority,
    isDraft: s.is_draft,
    // El `due_iso` viene del backend como estimate (created+1d) — siempre
    // estimated hasta que tengamos integración real de shipping.
    isDueEstimated: s.due_iso !== null,
  };
}

function computeOverdue(dueIso: string | null): boolean {
  if (!dueIso) return false;
  const today = new Date().toISOString().slice(0, 10);
  return dueIso < today;
}

function humanizeDue(dueIso: string | null): string {
  if (!dueIso) return "—";
  const today = new Date();
  const due = new Date(dueIso + "T00:00:00");
  const diffDays = Math.round(
    (due.getTime() - new Date(today.toISOString().slice(0, 10)).getTime()) /
      86_400_000,
  );
  if (diffDays === 0) return "hoy";
  if (diffDays === 1) return "mañana";
  if (diffDays === -1) return "ayer";
  if (diffDays > 0 && diffDays <= 7) {
    const dayNames = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
    return `${dayNames[due.getDay()]} ${due.getDate()}`;
  }
  return dueIso;
}

/* ── Queries ──────────────────────────────────────────────────────────── */

export function useOrders() {
  return useQuery<{ orders: Order[]; response: OrderListResponse }>({
    queryKey: orderKeys.list(),
    queryFn: async () => {
      try {
        const raw = await apiClient.get<unknown>("/api/orders/orders");
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
    // Cuando llegan nuevas órdenes desde Sales (vía `register_order`), la
    // app debería invalidar este key. Por ahora se refresca cada 30s.
    refetchInterval: 30_000,
    staleTime: 10_000,
    retry: 1, // Premortem C3: 1 retry rápido, luego fallback a empty.
  });
}

export function useOrderDetail(displayId: string | null) {
  return useQuery<OrderDetail>({
    queryKey: orderKeys.detail(displayId ?? "—"),
    queryFn: async () => {
      if (!displayId) throw new Error("displayId required");
      // Premortem A1: el backend ya resuelve `display_id` ("#1247") al
      // backend id internamente. Pasamos el display_id directo —
      // funciona para deep-links sin requerir useOrders() poblado antes.
      const raw = await apiClient.get<unknown>(
        `/api/orders/orders/${encodeURIComponent(displayId)}`,
      );
      return orderDetailSchema.parse(raw);
    },
    enabled: displayId !== null && displayId !== "",
    staleTime: 10_000,
  });
}

export function useVaultOrders() {
  return useQuery<VaultOrdersResponse>({
    queryKey: orderKeys.vault(),
    queryFn: async () => {
      try {
        const raw = await apiClient.get<unknown>("/api/orders/vault-orders");
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
    refetchInterval: 60_000, // menos frecuente que el kanban — es operacional.
    staleTime: 30_000,
    retry: 1,
  });
}
