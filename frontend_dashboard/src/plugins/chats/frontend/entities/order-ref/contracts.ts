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
