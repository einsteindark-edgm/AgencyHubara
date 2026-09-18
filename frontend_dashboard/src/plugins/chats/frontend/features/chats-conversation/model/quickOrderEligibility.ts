/**
 * ¿Tiene sentido ofrecer "Crear pedido" en esta conversación?
 *
 * Dos casos en el histórico (`status_history`), y ninguno más:
 *
 *  1. **`CONFIRMADO_SIN_DATOS`** — el cliente SÍ confirmó la compra pero NO
 *     completó los datos de envío (`tools/tags.py`; va siempre en combo con
 *     `escalate_to_human(ORDER_PENDING_SHIPPING_DETAILS)`). El humano entra a
 *     pedir los datos que faltan y después necesita registrar el pedido.
 *  2. **`INTERESADO` → `HUMANO`** (una entrada inmediatamente detrás de la
 *     otra) — un lead interesado que escalaron, o que el operador intervino,
 *     para cerrar la venta a mano. Solo cuenta la transición directa: pasar a
 *     HUMANO desde un `RECHAZO`, o un `INTERESADO` que llega después del
 *     `HUMANO`, no es un lead al borde de comprar.
 *
 * En cualquier otra conversación intervenida (una duda, un reclamo, un pedido
 * que ya cerró) el botón sería ruido en la barra del composer.
 *
 * Se mira el histórico y no el `tag` actual: al intervenir, el handoff escribe
 * `tag=HUMANO` encima, así que el estado que justifica el botón solo sobrevive
 * ahí. Que el pedido YA exista (`pending_payment_order_id`) lo decide el
 * composer, no este predicado.
 */

/** La escribe `ManageConversationTagTool` (backend `chats/agent/sales/tools/tags.py`). */
export const PENDING_SHIPPING_DATA_TAG = "CONFIRMADO_SIN_DATOS";
const INTERESTED_TAG = "INTERESADO";
const HUMAN_TAG = "HUMANO";

interface StatusHistoryEntry {
  tag: string;
}

export function canOfferQuickOrder(
  statusHistory?: readonly StatusHistoryEntry[] | null,
): boolean {
  if (!statusHistory) return false;
  return statusHistory.some(
    (entry, i) =>
      entry?.tag === PENDING_SHIPPING_DATA_TAG ||
      (entry?.tag === HUMAN_TAG && statusHistory[i - 1]?.tag === INTERESTED_TAG),
  );
}
