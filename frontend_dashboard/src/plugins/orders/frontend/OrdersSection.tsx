/**
 * `OrdersSection` — la Page del plugin orders. SOLO composición (F5.2):
 * la lógica de negocio vive en las features (orders-filters, orders-board,
 * orders-inspector, orders-vault-reconciliation).
 *
 * Datos reales: `useOrders()` consume `/api/orders/orders` (Medusa v2 vía
 * `MedusaOrderQuery`). Cuando Medusa no responde, `response.catalog_available`
 * es `false` y mostramos un estado vacío explícito.
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
import { VaultOrdersBanner } from "@plugins/orders/frontend/features/orders-vault-reconciliation";

import {
  useOrders,
  useOrdersEvents,
  useVaultOrders,
} from "@plugins/orders/frontend/entities/order";
import { MissingData } from "@/shared/ui";
import { usePluginHost, useSelection } from "@/shared/lib";

export function OrdersSection() {
  // F7: chrome + selección llegan por el PluginHost (contrato genérico).
  const { showSidebar, showInspector } = usePluginHost();
  const [selectedOrderId, setSelectedOrderId] = useSelection("orders", "#1247");
  useOrdersEvents(); // push del stream → invalidaciones (F1)
  const query = useOrders();
  const vaultQuery = useVaultOrders();
  const orders = query.data?.orders ?? [];
  const response = query.data?.response;
  const f = useOrderFilters(orders);
  // Counter "X órdenes · Y en valor" del header solo cuenta órdenes activas
  // (excluye canceladas). El kanban sigue mostrando la columna "Cancelada"
  // con sus cards — los counters reflejan productividad operacional, no
  // inventory total. Bug fix 2026-05-26.
  const filteredActive = f.filtered.filter((o) => o.status !== "cancelled");
  const filteredActiveCount = filteredActive.length;
  const filteredActiveTotal = filteredActive.reduce((a, b) => a + b.total, 0);
  const selected = orders.find((o) => o.id === selectedOrderId) ?? null;

  // Banner cuando Medusa no responde — el operador necesita saberlo.
  const showBackendUnavailable = response && !response.catalog_available;

  // Premortem F2+K1: pedidos en vault local pero NO en Medusa.
  const vaultRecords = vaultQuery.data?.records ?? [];
  const vaultFailedCount = vaultQuery.data?.failed_count ?? 0;
  const vaultStubCount = vaultQuery.data?.stub_count ?? 0;

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
          filteredCount={filteredActiveCount}
          filteredTotal={filteredActiveTotal}
          title={filterLabel(f.view)}
        />
        {showBackendUnavailable && (
          <div style={{ padding: "0 16px 8px" }}>
            <MissingData
              variant="block"
              label={
                response?.error_detail?.includes("medusa_unauthorized")
                  ? "Medusa: token expirado"
                  : "Medusa no está disponible"
              }
              reason={
                response?.error_detail ??
                "Verifica que el backend esté arriba y que MEDUSA_BASE_URL + MEDUSA_ADMIN_TOKEN estén configurados en .env."
              }
            />
          </div>
        )}
        {vaultRecords.length > 0 && (
          <VaultOrdersBanner
            failedCount={vaultFailedCount}
            stubCount={vaultStubCount}
            records={vaultRecords}
          />
        )}
        {!showBackendUnavailable && query.isLoading && (
          <div style={{ padding: 32, color: "var(--fg-muted)", textAlign: "center" }}>
            Cargando órdenes…
          </div>
        )}
        {!query.isLoading && orders.length === 0 && !showBackendUnavailable && (
          <div
            style={{
              padding: 32,
              color: "var(--fg-muted)",
              textAlign: "center",
              fontSize: 13,
            }}
          >
            No hay órdenes registradas en Medusa todavía. Cuando el agente
            de Sales cierre una venta con <code>register_order</code>, aparecerá
            aquí como Draft Order.
          </div>
        )}
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
