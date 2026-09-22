/**
 * ¿Tiene sentido ofrecer "Crear pedido" en esta conversación?
 *
 * Cuatro casos en el histórico (`status_history`), y ninguno más:
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
 *  3. **`RETOMA_VENTA` → `HUMANO`** (misma regla de transición directa) — la
 *     venta se retomó (vuelta de post-venta o del humano al bot) y la
 *     escalaron o la intervinieron para cerrarla a mano. También vale
 *     **`RETOMA_VENTA` → `RECHAZO` → `HUMANO`**: el cliente dijo que no a la
 *     venta retomada y el operador entró a rescatarla. Un `RECHAZO` que no
 *     viene de `RETOMA_VENTA` sigue sin contar.
 *  4. **Solo `HUMANO`** (una o más entradas, ningún otro tag) — el operador
 *     intervino desde el arranque o el cliente entró directo al humano: no
 *     hay ningún tag que descarte una venta.
 *
 * En cualquier otra conversación intervenida (un rechazo, un pedido que ya
 * cerró) el botón sería ruido en la barra del composer.
 *
 * Se mira el histórico y no el `tag` actual: al intervenir, el handoff escribe
 * `tag=HUMANO` encima, así que el estado que justifica el botón solo sobrevive
 * ahí. Que el pedido YA exista (`pending_payment_order_id`) lo decide el
 * composer, no este predicado.
 */

/** La escribe `ManageConversationTagTool` (backend `chats/agent/sales/tools/tags.py`). */
export const PENDING_SHIPPING_DATA_TAG = "CONFIRMADO_SIN_DATOS";
const INTERESTED_TAG = "INTERESADO";
/** La escriben `post_sale_return` y la devolución humano→bot (`platform/tools/routing.py`). */
const RESUMED_SALE_TAG = "RETOMA_VENTA";
/** Tags que, seguidos directamente de `HUMANO`, marcan una venta a cerrar a mano. */
const PRE_HANDOFF_SALE_TAGS: ReadonlySet<string> = new Set([INTERESTED_TAG, RESUMED_SALE_TAG]);
const REJECTED_TAG = "RECHAZO";
const HUMAN_TAG = "HUMANO";

interface StatusHistoryEntry {
  tag: string;
}

export function canOfferQuickOrder(
  statusHistory?: readonly StatusHistoryEntry[] | null,
): boolean {
  if (!statusHistory) return false;
  const tagAt = (i: number) => statusHistory[i]?.tag;
  const onlyHuman =
    statusHistory.length > 0 && statusHistory.every((entry) => entry?.tag === HUMAN_TAG);
  if (onlyHuman) return true;
  return statusHistory.some(
    (entry, i) =>
      entry?.tag === PENDING_SHIPPING_DATA_TAG ||
      (entry?.tag === HUMAN_TAG &&
        (PRE_HANDOFF_SALE_TAGS.has(tagAt(i - 1) ?? "") ||
          (tagAt(i - 1) === REJECTED_TAG && tagAt(i - 2) === RESUMED_SALE_TAG))),
  );
}
