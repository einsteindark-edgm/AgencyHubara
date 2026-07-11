/**
 * Panel de pedidos del cliente — SOLO en la app móvil, dentro del chat.
 *
 * Muestra los pedidos DE ESE cliente y deja cambiarles el estado a mano. Nada
 * de la sección orders de escritorio (tabla, filtros, kanban, detalle): solo la
 * mecánica de cambiar estado. Consume el cast propio de chats (`order-ref`),
 * nunca `@plugins/orders` (P-22).
 *
 * UX: por cada pedido, una tarjeta con su estado como pill de color y los
 * botones de las transiciones VÁLIDAS (guiado por el DAG — un tap, sin estados
 * inválidos). Cancelar (terminal) pide confirmación de dos pasos. Un error del
 * backend (transición inválida / falta fecha) se muestra inline en la tarjeta.
 */
import { useState } from "react";

import {
  NEXT_STAGES,
  ORDER_REF_STATUS_META,
  STAGE_ACTION_LABEL,
  useCustomerOrders,
  useTransitionOrderStage,
  type CustomerOrder,
  type OrderRefStatus,
} from "@plugins/chats/frontend/entities/order-ref";

interface Props {
  sessionId: string | null;
}

function shortId(id: string): string {
  return id.length > 8 ? `…${id.slice(-6)}` : id;
}

function formatCop(n: number): string {
  return `$${n.toLocaleString("es-CO")}`;
}

export function ChatsOrdersPanel({ sessionId }: Props) {
  const orders = useCustomerOrders(sessionId);
  const transition = useTransitionOrderStage(sessionId);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const onTransition = async (orderId: string, stage: OrderRefStatus) => {
    setBusyId(orderId);
    setErrors((e) => ({ ...e, [orderId]: "" }));
    try {
      const res = await transition.mutateAsync({ orderId, stage });
      if (!res.success) {
        setErrors((e) => ({
          ...e,
          [orderId]: res.error_detail ?? "No se pudo cambiar el estado.",
        }));
      }
    } catch (err) {
      setErrors((e) => ({
        ...e,
        [orderId]: err instanceof Error ? err.message : "Error de red.",
      }));
    } finally {
      setBusyId(null);
    }
  };

  if (orders.isLoading) {
    return <div className="orders-panel-state">Cargando pedidos…</div>;
  }
  if (orders.isError) {
    return (
      <div className="orders-panel-state" role="alert">
        No se pudieron cargar los pedidos.
        <button className="orders-retry" onClick={() => orders.refetch()}>
          Reintentar
        </button>
      </div>
    );
  }
  const list = orders.data?.orders ?? [];
  if (list.length === 0) {
    return (
      <div className="orders-panel-state">Este cliente no tiene pedidos.</div>
    );
  }

  return (
    <div className="orders-panel">
      <h3 className="orders-panel-title">Pedidos del cliente</h3>
      {list.map((o) => (
        <OrderCard
          key={o.id}
          order={o}
          busy={busyId === o.id}
          error={errors[o.id]}
          onTransition={onTransition}
        />
      ))}
    </div>
  );
}

interface CardProps {
  order: CustomerOrder;
  busy: boolean;
  error?: string;
  onTransition: (orderId: string, stage: OrderRefStatus) => void;
}

function OrderCard({ order, busy, error, onTransition }: CardProps) {
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const meta = ORDER_REF_STATUS_META[order.status];
  const nexts = NEXT_STAGES[order.status];
  const advances = nexts.filter((s) => s !== "cancelled");
  const canCancel = nexts.includes("cancelled");

  return (
    <div className="order-card">
      <div className="order-card-head">
        <span className="order-ref-id">{order.display_id ?? shortId(order.id)}</span>
        <span className={`ostat ostat-${meta.tone}`}>{meta.label}</span>
      </div>
      {(order.total_cop != null || order.due_iso) && (
        <div className="order-card-meta">
          {order.total_cop != null && <span>{formatCop(order.total_cop)}</span>}
          {order.due_iso && <span>Entrega: {order.due_iso}</span>}
        </div>
      )}

      {nexts.length === 0 ? (
        <div className="order-terminal">Sin más cambios de estado.</div>
      ) : confirmingCancel ? (
        <div className="order-card-actions" aria-label="Confirmar cancelación">
          <span className="order-confirm-q">¿Cancelar el pedido?</span>
          <button
            className="order-cancel-btn"
            disabled={busy}
            onClick={() => onTransition(order.id, "cancelled")}
          >
            Sí, cancelar
          </button>
          <button
            className="order-ghost-btn"
            onClick={() => setConfirmingCancel(false)}
          >
            No
          </button>
        </div>
      ) : (
        <div className="order-card-actions">
          {advances.map((s) => (
            <button
              key={s}
              className="order-advance-btn"
              disabled={busy}
              onClick={() => onTransition(order.id, s)}
            >
              {busy ? "…" : STAGE_ACTION_LABEL[s]}
            </button>
          ))}
          {canCancel && (
            <button
              className="order-cancel-btn"
              disabled={busy}
              onClick={() => setConfirmingCancel(true)}
            >
              Cancelar
            </button>
          )}
        </div>
      )}

      {error && (
        <div className="order-card-err" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}

export default ChatsOrdersPanel;
