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
