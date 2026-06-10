/**
 * `OrdersSection` — la Page del plugin orders.
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

import {
  useOrders,
  useVaultOrders,
  useRetryVaultOrder,
  useResolveVaultOrder,
  type VaultOrderRecord,
} from "@plugins/orders/frontend/entities/order";
import { Icon, MissingData } from "@/shared/ui";
import { usePluginHost, useSelection } from "@/shared/lib";

export function OrdersSection() {
  // F7: chrome + selección llegan por el PluginHost (contrato genérico).
  const { showSidebar, showInspector } = usePluginHost();
  const [selectedOrderId, setSelectedOrderId] = useSelection("orders", "#1247");
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

/* ── VaultOrdersBanner ─────────────────────────────────────────────────
 * Premortem F2+K1: surface visible de pedidos rechazados o stub que NO
 * están en Medusa. Sin esto, el operador podría perder ventas
 * silenciosamente.
 */

interface VaultOrdersBannerProps {
  failedCount: number;
  stubCount: number;
  records: VaultOrderRecord[];
}

function VaultOrdersBanner({
  failedCount,
  stubCount,
  records,
}: VaultOrdersBannerProps) {
  return (
    <div
      style={{
        margin: "0 16px 12px",
        padding: 12,
        borderRadius: 8,
        background: "rgba(255,180,74,0.08)",
        border: "1px solid rgba(255,180,74,0.35)",
        color: "var(--fg-soft)",
        fontSize: 12,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 8,
          color: "#ffb44a",
          fontWeight: 600,
        }}
      >
        <Icon.alert />
        <span>
          {records.length} pedido{records.length === 1 ? "" : "s"} pendiente
          {records.length === 1 ? "" : "s"} de reconciliar
          {failedCount > 0 && stubCount > 0
            ? ` (${failedCount} rechazado${failedCount === 1 ? "" : "s"} por Medusa, ${stubCount} local${stubCount === 1 ? "" : "es"})`
            : failedCount > 0
              ? ` (rechazado${failedCount === 1 ? "" : "s"} por Medusa)`
              : ` (registrado${stubCount === 1 ? "" : "s"} localmente sin Medusa)`}
        </span>
      </div>
      <details>
        <summary
          style={{
            cursor: "pointer",
            marginBottom: 6,
            color: "var(--fg-muted)",
          }}
        >
          Ver detalle
        </summary>
        <table
          style={{
            width: "100%",
            fontSize: 11,
            marginTop: 8,
            borderCollapse: "collapse",
          }}
        >
          <thead>
            <tr style={{ textAlign: "left", color: "var(--fg-muted)" }}>
              <th style={{ padding: "4px 8px" }}>Tipo</th>
              <th style={{ padding: "4px 8px" }}>Sesión</th>
              <th style={{ padding: "4px 8px" }}>Cliente</th>
              <th style={{ padding: "4px 8px" }}>Total</th>
              <th style={{ padding: "4px 8px" }}>Estado</th>
              <th style={{ padding: "4px 8px" }}>Acciones</th>
            </tr>
          </thead>
          <tbody>
            {records.slice(0, 10).map((r) => (
              <VaultOrderRow key={r.order_id} r={r} />
            ))}
          </tbody>
        </table>
        {records.length > 10 && (
          <div style={{ marginTop: 6, color: "var(--fg-muted)" }}>
            +{records.length - 10} más…
          </div>
        )}
      </details>
    </div>
  );
}

/* ── VaultOrderRow — una fila con acciones de reconciliación ───────────────
 * Cada fila tiene sus propios mutation hooks (estado per-fila): así el
 * spinner de "Reintentando…" afecta solo la fila tocada. "Reintentar" usa el
 * mismo núcleo idempotente que el barrido automático del backend; "Marcar
 * resuelto" saca el record del banner sin tocar Medusa (el operador ya lo
 * registró a mano). Tras resolver, la invalidación de `orderKeys.vault()`
 * hace que la fila desaparezca.
 */
function VaultOrderRow({ r }: { r: VaultOrderRecord }) {
  const retry = useRetryVaultOrder();
  const resolve = useResolveVaultOrder();
  const busy = retry.isPending || resolve.isPending;
  const outcome = retry.data?.outcome;

  const onResolve = () => {
    if (
      window.confirm(
        "¿Marcar este pedido como resuelto? Hacelo solo si ya lo registraste " +
          "en Medusa Admin — desaparecerá del banner.",
      )
    ) {
      resolve.mutate({ sessionKey: r.session_key, auditId: r.order_id });
    }
  };

  let estado = r.status === "abandoned" ? "Abandonado" : "Pendiente";
  if (retry.isError || resolve.isError) estado = "Error de red";
  else if (outcome === "still_failing") estado = "Medusa sigue caído";

  return (
    <tr style={{ borderTop: "1px solid rgba(255,255,255,0.04)" }}>
      <td style={{ padding: "4px 8px" }}>
        <span
          style={{
            padding: "1px 5px",
            borderRadius: 3,
            background:
              r.kind === "failed"
                ? "rgba(255,114,105,0.18)"
                : "rgba(214,138,255,0.18)",
            color: r.kind === "failed" ? "#ff7269" : "#d68aff",
            fontSize: 9,
            textTransform: "uppercase",
            fontWeight: 700,
          }}
        >
          {r.kind === "failed" ? "Rechazado" : "Local (stub)"}
        </span>
      </td>
      <td style={{ padding: "4px 8px", fontFamily: "var(--font-mono)" }}>
        {r.session_key}
      </td>
      <td style={{ padding: "4px 8px" }}>
        {r.customer_phone ?? "—"} · {r.customer_city ?? "—"}
      </td>
      <td style={{ padding: "4px 8px" }}>
        ${r.total_cop.toLocaleString("es-CO")} {r.currency}
      </td>
      <td
        style={{
          padding: "4px 8px",
          color: r.status === "abandoned" ? "#ff4d4d" : "var(--fg-muted)",
          maxWidth: 200,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={r.error_detail ?? ""}
      >
        {estado}
        {r.attempts > 0
          ? ` · ${r.attempts} intento${r.attempts === 1 ? "" : "s"}`
          : ""}
      </td>
      <td style={{ padding: "4px 8px", whiteSpace: "nowrap" }}>
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            retry.mutate({ sessionKey: r.session_key, auditId: r.order_id })
          }
          style={{
            fontSize: 10,
            padding: "2px 8px",
            borderRadius: 4,
            border: "1px solid rgba(255,255,255,0.14)",
            background: "transparent",
            color: "var(--fg-soft)",
            cursor: busy ? "wait" : "pointer",
            opacity: busy ? 0.5 : 1,
            marginRight: 4,
          }}
        >
          {retry.isPending ? "Reintentando…" : "Reintentar"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onResolve}
          style={{
            fontSize: 10,
            padding: "2px 8px",
            borderRadius: 4,
            border: "1px solid rgba(255,255,255,0.14)",
            background: "transparent",
            color: "var(--fg-soft)",
            cursor: busy ? "wait" : "pointer",
            opacity: busy ? 0.5 : 1,
          }}
        >
          Marcar resuelto
        </button>
      </td>
    </tr>
  );
}

export default OrdersSection;
