/**
 * ¿Tiene sentido ofrecer "Crear pedido" en esta conversación?
 *
 * Solo cuando el histórico trae **`CONFIRMADO_SIN_DATOS`**: la etiqueta que el
 * agente pone cuando el cliente SÍ confirmó la compra pero NO completó los
 * datos de envío (`tools/tags.py`; va siempre en combo con
 * `escalate_to_human(ORDER_PENDING_SHIPPING_DETAILS)`). Ése es exactamente el
 * agujero que el botón tapa: el humano entra a pedir los datos que faltan y
 * después no tiene cómo registrar el pedido.
 *
 * En cualquier otra conversación intervenida (una duda, un reclamo, un pedido
 * que ya cerró) el botón sería ruido en la barra del composer — por eso se
 * decide acá y no se muestra "por las dudas".
 *
 * La etiqueta se busca en TODO el `status_history`, no en el `tag` actual: al
 * intervenir, el handoff escribe `tag=HUMANO` encima, así que el estado que
 * justifica el botón solo sobrevive en el histórico.
 */

/** La escribe `ManageConversationTagTool` (backend `chats/agent/sales/tools/tags.py`). */
export const PENDING_SHIPPING_DATA_TAG = "CONFIRMADO_SIN_DATOS";

interface StatusHistoryEntry {
  tag: string;
}

export function canOfferQuickOrder(
  statusHistory?: readonly StatusHistoryEntry[] | null,
): boolean {
  if (!statusHistory) return false;
  return statusHistory.some((entry) => entry?.tag === PENDING_SHIPPING_DATA_TAG);
}
