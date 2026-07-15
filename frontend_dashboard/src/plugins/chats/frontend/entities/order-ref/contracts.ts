/**
 * `order-ref` — entity LOCAL de chats para el caso de uso "Confirmar pago".
 *
 * Canal 3 (PLUGIN_CONTRACT §5.3): chats NO adopta la entity `order` del
 * plugin orders. Define su propia vista mínima del resultado de comando que
 * el cast server-side (`/api/chats/order-actions/*` →
 * `src.plugins.chats.api.order_actions`) le devuelve. Si mañana el provider
 * cambia (otro `orders` con otro contrato), se ajusta el cast — este schema
 * y sus consumidores quedan intactos.
 */
import { z } from "zod";

/** Resultado plano de un comando de pedido vía el cast (espejo de order@v1). */
export const orderRefCommandResultSchema = z
  .object({
    success: z.boolean(),
    order_id: z.string().nullish(),
    current_stage: z.string().nullish(),
    error_detail: z.string().nullish(),
    audit_id: z.string().nullish(),
  })
  .passthrough();

export type OrderRefCommandResult = z.infer<typeof orderRefCommandResultSchema>;

/**
 * Vista mínima del read-side (`GET /api/chats/order-actions/{id}` → detalle
 * order@v1). Chats solo necesita saber si la entrega YA está agendada
 * (`summary.due_iso != null` = fecha real asignada por el operador; el
 * backend NO inventa estimates) para que "Confirmar pago" no re-agende.
 */
export const orderRefDetailSchema = z
  .object({
    summary: z
      .object({
        id: z.string().nullish(),
        due_iso: z.string().nullish(),
        due_time: z.string().nullish(),
        status: z.string().nullish(),
        is_draft: z.boolean().nullish(),
      })
      .passthrough(),
  })
  .passthrough();

export type OrderRefDetail = z.infer<typeof orderRefDetailSchema>;

/**
 * Estados del pedido (espejo mínimo de `OrderUiStatus` del backend — NO se
 * importa la entity de orders, P-22). El backend es la fuente de verdad de las
 * transiciones válidas; acá se duplica la vista mínima para el panel del chat.
 */
export const ORDER_REF_STATUSES = [
  "new",
  "preparing",
  "ready",
  "shipping",
  "delivered",
  "cancelled",
] as const;

export const orderRefStatusSchema = z.enum(ORDER_REF_STATUSES);
export type OrderRefStatus = z.infer<typeof orderRefStatusSchema>;

/** Un pedido del cliente en el panel del chat (subset de OrderSummaryDTO). */
export const customerOrderSchema = z
  .object({
    id: z.string(),
    display_id: z.string().nullish(),
    status: orderRefStatusSchema,
    total_cop: z.number().nullish(),
    currency_code: z.string().nullish(),
    due_iso: z.string().nullish(),
    is_draft: z.boolean().nullish(),
  })
  .passthrough();

export type CustomerOrder = z.infer<typeof customerOrderSchema>;

/** Respuesta de `GET /api/chats/order-actions/by-session/{id}`. */
export const customerOrdersSchema = z
  .object({
    orders: z.array(customerOrderSchema),
    count: z.number(),
  })
  .passthrough();

export type CustomerOrders = z.infer<typeof customerOrdersSchema>;
