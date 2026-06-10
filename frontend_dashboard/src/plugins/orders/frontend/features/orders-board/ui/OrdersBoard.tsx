/**
 * Kanban de Órdenes — único view del dashboard (no hay toggle a tabla/calendar
 * por feedback del usuario). Cada card muestra: ID, fecha, cliente, items,
 * total, badge de pago y, si está retrasada, banner rojo.
 *
 * Consume `orders` por prop porque el filtrado lo decide la página (que también
 * lo necesita para el KPI grid del header).
 */

import { useMemo, useState } from "react";
import {
  ORDER_STATUS_META,
  useTransitionOrderStage,
  type Order,
  type OrderStatus,
} from "@plugins/orders/frontend/entities/order";
import { Avatar, Icon, MacButton } from "@/shared/ui";
import { dayChipShort, fmtMoney } from "@/shared/lib";

const COLUMNS: OrderStatus[] = ["new", "preparing", "ready", "shipping", "delivered", "cancelled"];

// MIME type interno para el drag payload. Usamos un type custom para que
// el dataTransfer no choque con drops externos (imágenes, archivos).
const DRAG_MIME = "application/x-hubara-order";

interface Props {
  orders: Order[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function OrdersBoard({ orders, selectedId, onSelect }: Props) {
  const transition = useTransitionOrderStage();
  const [dropTarget, setDropTarget] = useState<OrderStatus | null>(null);
  // Toast local — si la transición falla (DAG violation, Medusa down),
  // mostramos el detail al operador. Auto-dismiss después de 5s vía CSS.
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const grouped = useMemo(() => {
    return COLUMNS.map((status) => ({
      status,
      meta: ORDER_STATUS_META[status],
      list: orders.filter((o) => o.status === status),
    }));
  }, [orders]);

  function handleDrop(toStage: OrderStatus, e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDropTarget(null);
    const orderId = e.dataTransfer.getData(DRAG_MIME);
    if (!orderId) return;
    const dragged = orders.find((o) => o.id === orderId);
    if (!dragged) return;
    if (dragged.status === toStage) return; // no-op (idempotente)

    transition.mutate(
      { orderId, to_stage: toStage },
      {
        onSuccess: (result) => {
          if (!result.success && result.error_detail) {
            // El backend rechazó (DAG violation, Medusa, etc.).
            // Optimistic update ya hizo rollback vía onError → onSettled.
            setErrorMsg(formatCommandError(result.error_detail));
          } else {
            setErrorMsg(null);
          }
        },
        onError: (err) => {
          setErrorMsg(`Error de red: ${err.message}`);
        },
      },
    );
  }

  return (
    <div className="kanban">
      {grouped.map(({ status, meta, list }) => {
        const total = list.reduce((a, b) => a + b.total, 0);
        const isHover = dropTarget === status;
        return (
          <div
            key={status}
            className="kcol"
            // Drop target: el header + body de cada columna.
            onDragOver={(e) => {
              // El default es "no drop allowed", así que tenemos que prevent.
              if (e.dataTransfer.types.includes(DRAG_MIME)) {
                e.preventDefault();
                e.dataTransfer.dropEffect = "move";
                if (dropTarget !== status) setDropTarget(status);
              }
            }}
            onDragLeave={() => {
              if (dropTarget === status) setDropTarget(null);
            }}
            onDrop={(e) => handleDrop(status, e)}
            style={
              isHover
                ? {
                    background: "rgba(255,255,255,0.04)",
                    outline: "2px dashed " + meta.color,
                    outlineOffset: -4,
                  }
                : undefined
            }
          >
            <div className="kcol-h">
              <span className="kc-dot" style={{ background: meta.color }} />
              <span className="kc-l">{meta.label}</span>
              <span className="kc-n">{list.length}</span>
              <span className="kc-t">{fmtMoney(total, true)}</span>
            </div>
            <div className="kcol-body">
              {list.map((o) => (
                <Card
                  key={o.id}
                  order={o}
                  selected={selectedId === o.id}
                  onSelect={onSelect}
                />
              ))}
              <button className="kadd">+ Agregar</button>
            </div>
          </div>
        );
      })}
      {errorMsg && (
        <div
          style={{
            position: "fixed",
            bottom: 24,
            right: 24,
            background: "rgba(255,114,105,0.15)",
            border: "1px solid rgba(255,114,105,0.4)",
            color: "#ff7269",
            padding: "10px 14px",
            borderRadius: 8,
            fontSize: 12,
            maxWidth: 360,
            zIndex: 1000,
            cursor: "pointer",
          }}
          onClick={() => setErrorMsg(null)}
          title="Click para cerrar"
        >
          {errorMsg}
        </div>
      )}
    </div>
  );
}

/** Traduce el `error_detail` del backend (formato `kind: detail`) a un
 * mensaje human-readable para el operador. */
function formatCommandError(detail: string): string {
  if (detail.startsWith("invalid_transition:")) {
    return `No se puede mover ahí: ${detail.replace("invalid_transition: ", "")}`;
  }
  if (detail.startsWith("not_found:")) {
    return "Esta orden ya no existe en Medusa.";
  }
  if (detail.startsWith("medusa_unauthorized:")) {
    return "El token de Medusa expiró. Rotalo desde Settings → Developer.";
  }
  if (detail.startsWith("medusa_unavailable:")) {
    return "Medusa no está disponible. Intentá en 1-2 minutos.";
  }
  return detail;
}

interface CardProps {
  order: Order;
  selected: boolean;
  onSelect: (id: string) => void;
}

function Card({ order, selected, onSelect }: CardProps) {
  return (
    <div
      className={"kcard" + (selected ? " sel" : "") + (order.isDraft ? " is-draft" : "")}
      onClick={() => onSelect(order.id)}
      // Drag and drop nativo HTML5 — sin deps externas.
      // Cards en columnas terminales (delivered, cancelled) NO son draggable
      // porque no pueden volver atrás sin force=True.
      draggable={order.status !== "delivered" && order.status !== "cancelled"}
      onDragStart={(e) => {
        e.dataTransfer.setData(DRAG_MIME, order.id);
        e.dataTransfer.effectAllowed = "move";
      }}
      // Premortem A2: borde lateral visible para distinguir Draft Orders
      // (cliente confirmó pero operador no procesó) de orders ya activas.
      style={
        order.isDraft
          ? { borderLeft: "3px solid #d68aff", cursor: "grab" }
          : { cursor: "grab" }
      }
    >
      <div className="kc-top">
        <span className="oid">
          {order.id}
          {/* Etiqueta "Pendiente agendar" para drafts en stage `new` —
              señaliza al operador que tiene que entrar al inspector y
              poner fecha + nota antes que la card avance al kanban. */}
          {order.isDraft && order.status === "new" && (
            <span
              style={{
                marginLeft: 6,
                fontSize: 8,
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: 0.4,
                padding: "1px 5px",
                borderRadius: 3,
                background: "rgba(255,180,74,0.18)",
                color: "#ffb44a",
                verticalAlign: "middle",
              }}
              title="Click para agendar la fecha de entrega y avanzar al stage 'En preparación'."
            >
              Pendiente agendar
            </span>
          )}
          {order.isDraft && order.status !== "new" && (
            <span
              style={{
                marginLeft: 6,
                fontSize: 8,
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: 0.4,
                padding: "1px 5px",
                borderRadius: 3,
                background: "rgba(214,138,255,0.18)",
                color: "#d68aff",
                verticalAlign: "middle",
              }}
              title="Draft Order — cliente confirmó la compra pero el operador todavía no la procesó."
            >
              Draft
            </span>
          )}
        </span>
        <span className="due-d">
          {dayChipShort(order.dueIso)} · {order.dueTime}
        </span>
      </div>
      <div className="kc-cust">
        <Avatar initials={order.short} color={order.color} size={26} />
        <div className="ocn">{order.customer}</div>
      </div>
      <div className="kc-meta">
        <span>
          {order.items} items · {order.pieces} und
        </span>
        <b>{fmtMoney(order.total)}</b>
      </div>
      <div className="kc-pay">
        {/* Pill de pago: refleja el ESTADO REAL del pago (payStatus, Medusa
            source of truth), NO la modalidad (payType). Si es "Contra entrega"
            (cod), mostramos esa modalidad porque pending es esperado hasta la
            entrega. Si no, mostramos paid/pending/partial/refund. */}
        {order.payStatus === "paid" ? (
          <span className="pay-badge confirmed">
            <Icon.check /> Pagado
          </span>
        ) : order.payType === "cod" ? (
          <span className="pay-badge cod">
            <Icon.truck /> Contra entrega
          </span>
        ) : order.payStatus === "partial" ? (
          <span className="pay-badge attention">
            <Icon.alert /> Pago parcial
          </span>
        ) : order.payStatus === "refund" ? (
          <span className="pay-badge attention">
            <Icon.alert /> Reembolsado
          </span>
        ) : (
          <span className="pay-badge attention">
            <Icon.alert /> Pago pendiente
          </span>
        )}
      </div>
      {order.overdue && (
        <div className="kc-over">
          <Icon.alert /> Retrasada
        </div>
      )}
    </div>
  );
}

/** Header del Kanban — KPIs + barra de conteo. Vive afuera para que la página
 *  pueda exhibir los KPIs aunque cambien filtros sin re-render del board. */
export function OrdersHeader({
  orders,
  filteredCount,
  filteredTotal,
  title,
}: {
  orders: Order[];
  filteredCount: number;
  filteredTotal: number;
  title: string;
}) {
  const today = new Date().toISOString().slice(0, 10);
  // YYYY-MM del mes actual — usado para filtrar dueIso del mes corriente.
  const monthPrefix = today.slice(0, 7);
  const k = useMemo(
    () => {
      // Bug fix 2026-05-26: TODOS los KPIs operacionales excluyen órdenes
      // canceladas. La cancelada vive en su columna del kanban pero NO
      // contamina las métricas de productividad (today/overdue/inProc/etc).
      // Las canceladas tienen su propio counter por columna en el kanban.
      const active = orders.filter((o) => o.status !== "cancelled");
      // "Para hoy" = órdenes ACTIVAS con fecha de entrega EXACTAMENTE hoy.
      const todayCount = active.filter(
        (o) => !!o.dueIso && o.dueIso === today,
      ).length;
      // "Sin agendar" = órdenes activas sin fecha de entrega asignada.
      const unscheduled = active.filter((o) => !o.dueIso).length;
      // "En proceso" = preparing + ready (no terminal, no new-sin-agendar).
      const inProc = active.filter((o) =>
        (["preparing", "ready"] as OrderStatus[]).includes(o.status),
      ).length;
      // "En tránsito" = en camino.
      const shipping = active.filter((o) => o.status === "shipping").length;
      // "Ingresos mes" = total de órdenes activas con fecha de entrega
      // dentro del mes corriente. Cancelled ya filtrado arriba.
      const revenueMonth = active
        .filter(
          (o) => !!o.dueIso && o.dueIso.startsWith(monthPrefix),
        )
        .reduce((a, b) => a + b.total, 0);
      return {
        todayCount,
        unscheduled,
        overdue: active.filter((o) => o.overdue).length,
        inProc,
        shipping,
        revenueMonth,
      };
    },
    [orders, today, monthPrefix],
  );

  return (
    <div className="ord-head">
      <div className="ord-h-top">
        <div>
          <h1>{title}</h1>
          <p className="sub">
            {filteredCount} órdenes · {fmtMoney(filteredTotal)} en valor
          </p>
        </div>
        <div className="ord-actions">
          <MacButton ghost sm>
            <Icon.filter /> Filtros
          </MacButton>
          <MacButton ghost sm>
            <Icon.download /> Exportar
          </MacButton>
          <MacButton ghost sm>
            <Icon.more />
          </MacButton>
          <MacButton primary sm>
            <Icon.plus /> Nueva orden
          </MacButton>
        </div>
      </div>

      <div className="kpi-row">
        <KPI label="Para hoy"     value={k.todayCount}             tone="accent" sub="Entrega agendada hoy" />
        <KPI label="Sin agendar"  value={k.unscheduled}            tone="orange" sub="Pendientes de asignar fecha" />
        <KPI label="Retrasadas"   value={k.overdue}                tone="red"    sub="Requieren atención" />
        <KPI label="En proceso"   value={k.inProc}                 tone="orange" sub="Preparando + listas" />
        <KPI label="En tránsito"  value={k.shipping}               tone="cyan" />
        <KPI label="Ingresos mes" value={fmtMoney(k.revenueMonth, true)} tone="green" sub="Entregas agendadas este mes" />
      </div>

      <div className="ord-bar">
        <span className="ord-bar-count">{filteredCount} órdenes</span>
      </div>
    </div>
  );
}

function KPI({
  label,
  value,
  tone,
  sub,
}: {
  label: string;
  value: string | number;
  tone: "accent" | "red" | "orange" | "green" | "cyan";
  sub?: string;
}) {
  return (
    <div className={"kpi tone-" + tone}>
      <div className="kl">{label}</div>
      <div className="kv">{value}</div>
      {sub && <div className="ks">{sub}</div>}
    </div>
  );
}
