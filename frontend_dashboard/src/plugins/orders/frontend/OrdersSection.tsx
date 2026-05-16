/**
 * `OrdersSection` — la Page del plugin orders.
 *
 * Extraída de `pages/Dashboard.tsx` en PR7 (refactor a plugins). Mantiene la
 * firma de props que tenía cuando vivía inline en el shell.
 *
 * Plugin frontend-only — los datos (`order`) viven en `entities/` (shared
 * cross-plugin). Cuando se necesite backend (CRUD de órdenes, integración
 * con shipping providers, etc.), se agregará `agent` y/o `api` al manifest.
 */
import {
  OrdersFilters,
  filterLabel,
  useOrderFilters,
} from "@plugins/orders/frontend/features/orders-filters";
import {
  OrdersBoard,
  OrdersHeader,
} from "@plugins/orders/frontend/features/orders-board";
import { OrdersInspector } from "@plugins/orders/frontend/features/orders-inspector";

import { useOrders } from "@/entities/order";

export interface OrdersSectionProps {
  showSidebar: boolean;
  showInspector: boolean;
  selectedOrderId: string | null;
  setSelectedOrderId: (id: string) => void;
}

export function OrdersSection({
  showSidebar,
  showInspector,
  selectedOrderId,
  setSelectedOrderId,
}: OrdersSectionProps) {
  const { data: orders = [] } = useOrders();
  const f = useOrderFilters(orders);
  const filteredTotal = f.filtered.reduce((a, b) => a + b.total, 0);
  const selected = orders.find((o) => o.id === selectedOrderId) ?? null;

  return (
    <>
      {showSidebar && (
        <OrdersFilters
          view={f.view}
          setView={f.setView}
          payType={f.payType}
          setPayType={f.setPayType}
          orders={orders}
        />
      )}
      <main className="ord-canvas">
        <OrdersHeader
          orders={orders}
          filteredCount={f.filtered.length}
          filteredTotal={filteredTotal}
          title={filterLabel(f.view)}
        />
        <div className="ord-body">
          <OrdersBoard
            orders={f.filtered}
            selectedId={selectedOrderId}
            onSelect={setSelectedOrderId}
          />
        </div>
      </main>
      {showInspector && <OrdersInspector order={selected} />}
    </>
  );
}

export default OrdersSection;
