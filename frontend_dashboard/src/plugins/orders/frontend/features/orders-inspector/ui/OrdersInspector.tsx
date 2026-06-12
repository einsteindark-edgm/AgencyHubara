/**
 * Inspector de Órdenes (panel derecho). Top: header con ID/cliente/pills/
 * acciones. Body: ReadyForShip + paneles colapsables (Línea de tiempo,
 * Productos, Entrega, Pago, Notas, Historial cliente).
 *
 * Datos reales: viene de `useOrderDetail(displayId)` que consulta el
 * backend `/api/orders/orders/{id}` (Medusa v2 vía MedusaOrderQuery).
 *
 * Datos que Medusa NO tiene todavía (timeline detallado, notas internas,
 * historial cliente, agente asignado) se renderizan con el marker
 * `MissingData` para que el operador vea explícitamente qué falta integrar.
 */

import {
  ORDER_STATUS_META,
  useOrderDetail,
  type Order,
} from "@plugins/orders/frontend/entities/order";
import { Icon, MacButton, MissingData } from "@/shared/ui";
import { CustomerHistoryPanel } from "./CustomerHistoryPanel";
import { DangerPanel } from "./DangerPanel";
import { DeliveryPanel } from "./DeliveryPanel";
import { ItemsPanel } from "./ItemsPanel";
import { NotesPanel } from "./NotesPanel";
import { PaymentPanel } from "./PaymentPanel";
import { ReadyForShip } from "./ReadyForShip";
import { TimelinePanel } from "./TimelinePanel";

interface Props {
  order: Order | null;
}

export function OrdersInspector({ order }: Props) {
  // Llamar el hook SIEMPRE (no condicional) — pasa null cuando no hay
  // selección y el hook lo desactiva internamente. Cumple Rules of Hooks.
  const detailQuery = useOrderDetail(order?.id ?? null);

  if (!order) {
    return (
      <aside
        className="inspector"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--fg-muted)",
          padding: 24,
          textAlign: "center",
        }}
      >
        <p style={{ fontSize: 12 }}>Selecciona una orden para ver sus detalles</p>
      </aside>
    );
  }

  const statusMeta = ORDER_STATUS_META[order.status];
  const detail = detailQuery.data;
  const missing = new Set(detail?.data_completeness_missing ?? []);

  return (
    <aside className="inspector">
      <div className="ins-head">
        <div className="ih-title">
          <h3>
            {order.id}
            {order.isDraft && (
              <span
                style={{
                  marginLeft: 8,
                  fontSize: 9,
                  fontWeight: 600,
                  textTransform: "uppercase",
                  letterSpacing: 0.5,
                  padding: "2px 6px",
                  borderRadius: 4,
                  background: "var(--color-violet-soft)",
                  color: "var(--color-violet)",
                  verticalAlign: "middle",
                }}
              >
                Draft
              </span>
            )}
          </h3>
          <p>{order.customer}</p>
        </div>
        <div className="oi-pills">
          <span
            className="ord-pill"
            style={{ background: statusMeta.bg, color: statusMeta.color }}
          >
            <span className="d" style={{ background: statusMeta.color }} />
            {statusMeta.label}
          </span>
          <span
            className="ord-pill"
            style={{
              background: "rgba(255,255,255,0.06)",
              color: "var(--fg-soft)",
            }}
          >
            {order.channel}
          </span>
        </div>
        <div className="oi-actions">
          <MacButton primary sm style={{ flex: 1 }}>
            Avanzar estado
          </MacButton>
          <MacButton ghost sm>
            <Icon.msg />
          </MacButton>
          <MacButton ghost sm>
            <Icon.phone />
          </MacButton>
          <MacButton ghost sm>
            <Icon.more />
          </MacButton>
        </div>
      </div>

      <div className="ins-body">
        {/* "Lista para envío" — único panel de agendamiento. Solo aparece
            para orders nuevas sin fecha asignada (acción primaria del
            operador antes de avanzar al kanban). Una vez agendada,
            desaparece. */}
        {order.status === "new" && !order.dueIso && (
          <ReadyForShip order={order} />
        )}

        {detailQuery.isLoading && (
          <div style={{ padding: 16, color: "var(--fg-muted)", fontSize: 12 }}>
            Cargando detalle…
          </div>
        )}

        {detailQuery.isError && (
          <div style={{ padding: 16 }}>
            <MissingData
              variant="block"
              label="No se pudo cargar el detalle"
              reason="Medusa no respondió. Recarga la página o verifica que el backend esté arriba."
            />
          </div>
        )}

        {detail && (
          <>
            <TimelinePanel detail={detail} order={order} missing={missing} />
            <ItemsPanel detail={detail} />
            <DeliveryPanel detail={detail} missing={missing} order={order} />
            <PaymentPanel detail={detail} missing={missing} order={order} />
            <NotesPanel detail={detail} />
            <DangerPanel order={order} />
            <CustomerHistoryPanel order={order} />
          </>
        )}
      </div>
    </aside>
  );
}

/* SchedulePanel removed (2026-05-26) — merged into `ReadyForShip` as the
   single, canonical scheduling form. Wiring lives in `./ReadyForShip.tsx`. */
