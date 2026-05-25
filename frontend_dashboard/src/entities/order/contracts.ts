/**
 * Schemas Zod para validar respuestas del backend de `orders`.
 *
 * Backend: plugin `orders.api` (FastAPI) sirve `/api/orders/orders` (list)
 * y `/api/orders/orders/{id}` (detail). Datos vienen de Medusa v2
 * (`/admin/orders` + `/admin/draft-orders`) traducidos por
 * `MedusaOrderQuery`.
 *
 * Campos que NO vienen del backend hoy se marcan en el array
 * `data_completeness_missing[]` del detail. La UI pinta un marker
 * "Datos pendientes" sobre esos slots.
 */

import { z } from "zod";

/* ── Status enums — match exacto con OrderUiStatus del backend ─────────── */

export const orderUiStatusSchema = z.enum([
  "new",
  "preparing",
  "ready",
  "shipping",
  "delivered",
  "cancelled",
]);
export const orderPayStatusSchema = z.enum(["paid", "partial", "pending", "refund"]);
export const orderPayTypeSchema = z.enum(["cod", "confirmed"]);
export const orderPrioritySchema = z.enum(["alta", "normal", "baja"]);
export const orderColorSchema = z.enum(["a", "b", "c", "d", "e", "f"]);

/* ── OrderSummary — shape devuelto por /api/orders/orders ──────────────── */

export const orderSummarySchema = z.object({
  id: z.string(),
  display_id: z.string(),
  customer: z.string(),
  short: z.string(),
  color: orderColorSchema,
  phone: z.string().nullable(),
  city: z.string().nullable(),
  channel: z.string(),
  status: orderUiStatusSchema,
  pay_status: orderPayStatusSchema,
  pay_type: orderPayTypeSchema,
  items: z.number().int(),
  pieces: z.number().int(),
  total_cop: z.number().int(),
  currency_code: z.string(),
  is_draft: z.boolean(),
  // Datos que Medusa NO tiene todavía — son estimaciones / placeholders.
  due_iso: z.string().nullable(),
  due_time: z.string().nullable(),
  overdue: z.boolean(),
  priority: orderPrioritySchema,
  agent: z.string(),
  created_at_ms: z.number(),
  updated_at_ms: z.number(),
});

export type OrderSummary = z.infer<typeof orderSummarySchema>;

/* ── OrderList — envelope de la list ───────────────────────────────────── */

export const orderListResponseSchema = z.object({
  orders: z.array(orderSummarySchema),
  count: z.number().int(),
  offset: z.number().int(),
  limit: z.number().int(),
  // false = Medusa no responde / no configurado. El frontend pinta estado
  // vacío explícito (NO error).
  catalog_available: z.boolean(),
  error_detail: z.string().nullable(),
});

export type OrderListResponse = z.infer<typeof orderListResponseSchema>;

/* ── OrderItemDetail — line item del inspector ─────────────────────────── */

export const orderItemDetailSchema = z.object({
  title: z.string(),
  sku: z.string().nullable(),
  quantity: z.number().int(),
  unit_price_cop: z.number().int(),
  total_cop: z.number().int(),
  variant_label: z.string().nullable(),
  thumbnail: z.string().nullable(),
  handle: z.string().nullable(),
});

export type OrderItemDetail = z.infer<typeof orderItemDetailSchema>;

/* ── OrderAddress ──────────────────────────────────────────────────────── */

export const orderAddressSchema = z.object({
  first_name: z.string().nullable(),
  last_name: z.string().nullable(),
  phone: z.string().nullable(),
  address_1: z.string().nullable(),
  address_2: z.string().nullable(),
  city: z.string().nullable(),
  country_code: z.string().nullable(),
});

export type OrderAddress = z.infer<typeof orderAddressSchema>;

/* ── Timeline event ─────────────────────────────────────────────────────── */

export const orderTimelineEventSchema = z.object({
  type: z.string(),
  label: z.string(),
  timestamp_ms: z.number(),
  detail: z.string().nullable(),
});

export type OrderTimelineEvent = z.infer<typeof orderTimelineEventSchema>;

/* ── OrderDetail — shape devuelto por /api/orders/orders/{id} ──────────── */

export const orderDetailSchema = z.object({
  summary: orderSummarySchema,
  items_detail: z.array(orderItemDetailSchema),
  shipping_address: orderAddressSchema.nullable(),
  billing_address: orderAddressSchema.nullable(),
  subtotal_cop: z.number().int(),
  shipping_cop: z.number().int(),
  tax_total_cop: z.number().int(),
  discount_total_cop: z.number().int(),
  timeline: z.array(orderTimelineEventSchema),
  payment_method_label: z.string().nullable(),
  notes: z.array(z.string()),
  // Lista de slots que aún no vienen del backend. La UI pinta un marker
  // "Datos pendientes" sobre estos campos. Valores conocidos:
  // 'due_date', 'agent', 'priority', 'notes', 'customer_history',
  // 'tracking_number', 'shipping_provider', 'payment_method_detail'.
  data_completeness_missing: z.array(z.string()),
});

export type OrderDetail = z.infer<typeof orderDetailSchema>;

// Vault orders — premortem F2+K1.
// Orders que existen en el vault local (hubara_vault/wa_<id>/metadata.json)
// pero NO en Medusa (failed registrations + stub orders). Sin este endpoint,
// el operador podria perder ventas silenciosamente.

export const vaultOrderKindSchema = z.enum(["failed", "stub"]);

export const vaultOrderRecordSchema = z.object({
  kind: vaultOrderKindSchema,
  session_key: z.string(),
  order_id: z.string(),
  customer_phone: z.string().nullable(),
  customer_city: z.string().nullable(),
  total_cop: z.number().int(),
  currency: z.string(),
  items_count: z.number().int(),
  payment_method: z.string().nullable(),
  error_detail: z.string().nullable(),
  registered_at_ms: z.number(),
  raw: z.record(z.string(), z.unknown()).default({}),
});

export type VaultOrderRecord = z.infer<typeof vaultOrderRecordSchema>;

export const vaultOrdersResponseSchema = z.object({
  records: z.array(vaultOrderRecordSchema),
  count: z.number().int(),
  failed_count: z.number().int(),
  stub_count: z.number().int(),
});

export type VaultOrdersResponse = z.infer<typeof vaultOrdersResponseSchema>;
