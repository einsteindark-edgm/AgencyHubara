/**
 * Contratos del formulario "Crear pedido" del chat intervenido.
 *
 * Dos endpoints PROPIOS de chats (nada cross-plugin — P-9/P-23):
 *   1. `POST /api/chats/order-intake/{session}/suggest` — lee la conversación
 *      con DeepSeek y devuelve el formulario PRE-LLENADO (no registra nada).
 *   2. `POST /api/chats/session-actions/{session}/order` — el registro real
 *      (precia server-side, crea el draft en Medusa y cierra "pago pendiente").
 *
 * El resultado del registro se valida con `.default(...)` en casi todo: la
 * respuesta de FALLO trae solo 3-4 campos (`registered:false` + `error_detail`),
 * y queremos que el modal muestre el error en vez de tronar en el `.parse`.
 */

import { z } from "zod";

/** De dónde salió cada campo del formulario (badge en la UI). */
export const intakeFieldSourceSchema = z
  .enum(["conversation", "draft", "session"])
  .nullable()
  .default(null);

export const orderIntakeItemSchema = z.object({
  handle: z.string(),
  title: z.string(),
  variant_label: z.string().nullable().default(null),
  quantity: z.number(),
  unit_price_cop: z.number(),
  line_total_cop: z.number(),
  /** `false` = el modelo no pudo fijar la variante (el operador debe elegirla). */
  variant_resolved: z.boolean().default(false),
  /** Cita textual del cliente que justifica el ítem. */
  evidence: z.string().nullable().default(null),
  /** Color/aroma resuelto del borrador estructurado del chat (`null` = sin resolver). */
  color: z.string().nullable().default(null),
  aroma: z.string().nullable().default(null),
  /** Listas CERRADAS del producto (`[]` = el producto no tiene ese atributo). */
  colors: z.array(z.string()).default([]),
  aromas: z.array(z.string()).default([]),
  /** Cupo por unidad: cuántas unidades de la línea llevan el descuento del cupón. */
  coupon_units: z.number().default(0),
  /** Descuento del cupón sobre esta línea (COP). */
  coupon_discount_cop: z.number().default(0),
});

export const orderIntakeShippingSchema = z.object({
  city: z.string().nullable().default(null),
  neighborhood: z.string().nullable().default(null),
  address: z.string().nullable().default(null),
  phone: z.string().nullable().default(null),
  receiver_name: z.string().nullable().default(null),
  national_id: z.string().nullable().default(null),
});

export const catalogOptionSchema = z.object({
  handle: z.string(),
  title: z.string(),
  variants: z.array(
    z.object({ label: z.string(), unit_price_cop: z.number() }),
  ),
  /** Listas CERRADAS del producto para una línea agregada a mano (`[]` = no aplica). */
  colors: z.array(z.string()).default([]),
  aromas: z.array(z.string()).default([]),
});

export const paymentMethodSchema = z.enum([
  "transfer",
  "payment_link",
  "cash_on_delivery",
]);

export const orderSuggestionSchema = z.object({
  session_key: z.string(),
  phone_number: z.string(),
  handoff_at_ms: z.number().nullable().default(null),
  messages_considered: z.number().default(0),
  items: z.array(orderIntakeItemSchema).default([]),
  shipping: orderIntakeShippingSchema,
  field_sources: z.record(z.string(), intakeFieldSourceSchema).default({}),
  payment_method: paymentMethodSchema.nullable().default(null),
  subtotal_cop: z.number().default(0),
  shipping_cop: z.number().default(0),
  /** Cupón aplicado en el chat (`apply_coupon`): el registro lo descuenta. */
  discount_cop: z.number().default(0),
  coupon_code: z.string().nullable().default(null),
  total_cop: z.number().default(0),
  /** Campos que el operador TIENE que completar antes de poder registrar. */
  missing: z.array(z.string()).default([]),
  warnings: z.array(z.string()).default([]),
  notes: z.string().nullable().default(null),
  /** Lista cerrada de productos: el selector para corregir lo que eligió el modelo. */
  catalog: z.array(catalogOptionSchema).default([]),
  /** La sesión YA tiene un pedido registrado (aviso anti-duplicado). */
  already_registered_order_id: z.string().nullable().default(null),
  model: z.string().default(""),
  /** El LLM no respondió: el formulario abre con lo que anotó el bot. */
  degraded: z.boolean().default(false),
  error_detail: z.string().nullable().default(null),
});

export const createOrderResultSchema = z.object({
  registered: z.boolean(),
  already_registered: z.boolean().default(false),
  order_id: z.string().nullable().default(null),
  /** Referencia humana ("#22 (Dúo Zodiacal)") — lo que el operador le dice al cliente. */
  order_reference: z.string().nullable().default(null),
  error_detail: z.string().nullable().default(null),
  problems: z.array(z.string()).default([]),
  subtotal_cop: z.number().nullable().default(null),
  shipping_cop: z.number().nullable().default(null),
  /** Solo en `quota_changed`: el descuento recalculado del cupón. */
  discount_cop: z.number().nullable().default(null),
  total_cop: z.number().nullable().default(null),
  payment_instructions_sent: z.boolean().default(false),
  /** El intento quedó guardado y la reconciliación lo reintenta sola. */
  saved_for_retry: z.boolean().default(false),
});

export type OrderIntakeItem = z.infer<typeof orderIntakeItemSchema>;
export type OrderIntakeShipping = z.infer<typeof orderIntakeShippingSchema>;
export type OrderSuggestion = z.infer<typeof orderSuggestionSchema>;
export type CatalogOption = z.infer<typeof catalogOptionSchema>;
export type PaymentMethod = z.infer<typeof paymentMethodSchema>;
export type CreateOrderResult = z.infer<typeof createOrderResultSchema>;
export type IntakeFieldSource = z.infer<typeof intakeFieldSourceSchema>;
