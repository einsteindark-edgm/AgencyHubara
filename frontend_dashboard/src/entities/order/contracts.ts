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
  // H3: el LLM pidió una variante que NO matcheó ninguna variante real del
  // producto en Medusa (ej. producto solo tiene "Unico" pero el LLM mandó
  // "Ylan ylang, Gris"). Cuando true, la UI debe destacar al operador que
  // revise manualmente — la variante registrada en Medusa NO refleja lo
  // pedido. Default false para tolerar payloads viejos pre-feature.
  variant_label_mismatch: z.boolean().default(false),
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
  // "Datos pendientes" sobre estos campos. Valores conocidos hoy:
  // 'agent', 'customer_history', 'due_date', 'tracking_number',
  // 'shipping_provider', 'payment_method_detail'. Lista colapsable según
  // el operador progresa el flujo.
  data_completeness_missing: z.array(z.string()),
});

export type OrderDetail = z.infer<typeof orderDetailSchema>;

/* ── Command response — shape de los endpoints write-side (F7) ───────────
 *
 * Los 4 endpoints PATCH/POST devuelven SIEMPRE este shape — el componente
 * que llama al mutation hace switch sobre `success` y `error_detail`.
 *
 *   success=true  → invalidate queryKey list+detail; UI muestra toast OK.
 *   success=false → muestra dialog con el `error_detail`. Kinds canónicos:
 *      - "not_found: ..."          (HTTP 404 disfrazado de 200 con success=false)
 *      - "invalid_transition: ..." (DAG violation; UI muestra dialog explicativo)
 *      - "medusa_unauthorized: ..."(token expirado; pedile al operador rotar)
 *      - "medusa_unavailable: ..." (5xx / no config; retry en 1-2 min)
 *      - "medusa_api_error: ..."   (cualquier otro 4xx/5xx)
 */

export const orderCommandResultSchema = z.object({
  success: z.boolean(),
  order_id: z.string(),
  current_stage: orderUiStatusSchema.nullable(),
  error_detail: z.string().nullable(),
  audit_id: z.string().nullable(),
});

export type OrderCommandResult = z.infer<typeof orderCommandResultSchema>;

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
  // Estado del loop de reconciliación. El backend filtra los `resolved` por
  // default, así que el banner normalmente ve `pending` | `abandoned`.
  status: z.enum(["pending", "resolved", "abandoned"]).default("pending"),
  attempts: z.number().int().default(0),
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

// Resultado de POST /vault-orders/{session_key}/{audit_id}/retry|resolve.
export const reconciliationOutcomeSchema = z.object({
  session_key: z.string(),
  audit_id: z.string(),
  outcome: z.enum([
    "resolved",
    "still_failing",
    "already_resolved",
    "abandoned",
    "not_found",
    "error",
  ]),
  resolved_order_id: z.string().nullable(),
  provider: z.string().nullable(),
  error_detail: z.string().nullable(),
  attempts: z.number().int(),
});

export type ReconciliationOutcome = z.infer<typeof reconciliationOutcomeSchema>;

/* ── Customer Score — panel "Historial cliente" del inspector ────────────
 *
 * Backend: GET /api/orders/orders/{id}/customer-score
 *
 * El backend computa el score del cliente cruzando episodes del vault con
 * orders de Medusa. Determinístico — sale del rules.yaml editable en
 * caliente. Para clientes sin historial (sesión vacía o sin phone), backend
 * devuelve tag="Sin datos" + letter="—" para que el frontend pinte
 * MissingData.
 *
 * `rules_version` permite auditar "¿con qué rules se computó esto?". Si
 * después de editar rules.yaml el score cambia, el version bump es visible.
 */

export const scoreBreakdownItemSchema = z.object({
  feature: z.string(),
  feature_value: z.number(),
  points: z.number().int(),
});

export type ScoreBreakdownItem = z.infer<typeof scoreBreakdownItemSchema>;

export const customerScoreSchema = z.object({
  tag: z.string(),                                  // VIP / Recurrente / Nuevo / Frío / Estándar / Sin datos
  score_letter: z.string(),                         // A / B / C / D / —
  score_value: z.number().int(),
  score_reason: z.string(),
  monetary_cop: z.number().int(),
  last_purchase_at_ms: z.number().nullable(),
  last_purchase_iso: z.string().nullable(),         // YYYY-MM-DD (helper backend)
  // 5° KV: "X compras de Y episodios" — conversion rate del cliente.
  // .default() para tolerar respuestas backend viejas (pre-feature) sin romper.
  frequency_total: z.number().int().default(0),     // # COMPRA_EXITOSA
  episodes_total: z.number().int().default(0),      // # episodios totales
  rules_version: z.number().int(),
  breakdown: z.array(scoreBreakdownItemSchema),
  session_id: z.string().nullable(),                // debug — `wa_<phone>` o null
});

export type CustomerScore = z.infer<typeof customerScoreSchema>;

/* ── Customer Summary — botón "Resumir con IA" on-demand ─────────────────
 *
 * Backend: POST /api/orders/orders/{id}/customer-summary
 *
 * Llama Gemini Flash via litellm con el score determinístico + episodes.
 * Degrada gracefully: si el LLM falla, devuelve summary fallback con
 * `error_detail` poblado (UI muestra warning pero igual renderea texto).
 */

export const customerSummarySchema = z.object({
  summary: z.string(),
  model: z.string(),
  latency_ms: z.number().int(),
  rules_version: z.number().int(),
  error_detail: z.string().nullable(),
});

export type CustomerSummary = z.infer<typeof customerSummarySchema>;
