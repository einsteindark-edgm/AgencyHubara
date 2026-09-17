/**
 * Aviso "valores de pedidos sin actualizar".
 *
 * El revenue / ticket / valor de Ads es el valor del pedido en Orders
 * (OrderFacts, pedido #31). Si Orders (Medusa) no respondió, el backend manda
 * el último valor conocido con `orders_stale=true`; acá se le avisa al
 * operador para que no tome esos números como definitivos.
 */
import {
  useAdsOrdersStale,
  type AdsWindowParams,
} from "@plugins/ads/frontend/entities/ads-campaign";

export function OrdersStaleNotice({ params }: { params: AdsWindowParams }) {
  const stale = useAdsOrdersStale(params);
  if (!stale) return null;
  return (
    <div
      role="status"
      className="mx-4 mt-3 rounded-md border px-3 py-2 text-xs"
      style={{
        background: "rgba(255,180,60,0.12)",
        borderColor: "rgba(255,180,60,0.4)",
        color: "var(--color-warning, #d97706)",
      }}
    >
      Valores de pedidos sin actualizar: Orders no respondió y se muestra el
      último valor conocido. Se actualiza solo cuando Orders vuelva.
    </div>
  );
}
