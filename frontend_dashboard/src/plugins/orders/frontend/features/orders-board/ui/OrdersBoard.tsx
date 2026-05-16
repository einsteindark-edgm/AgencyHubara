/**
 * Kanban de Órdenes — único view del dashboard (no hay toggle a tabla/calendar
 * por feedback del usuario). Cada card muestra: ID, fecha, cliente, items,
 * total, badge de pago y, si está retrasada, banner rojo.
 *
 * Consume `orders` por prop porque el filtrado lo decide la página (que también
 * lo necesita para el KPI grid del header).
 */

import { useMemo } from "react";
import {
  ORDER_STATUS_META,
  type Order,
  type OrderStatus,
} from "@/entities/order";
import { Avatar, Icon, MacButton } from "@/shared/ui";
import { dayChipShort, fmtMoney } from "@/shared/lib";

const COLUMNS: OrderStatus[] = ["new", "preparing", "ready", "shipping", "delivered", "cancelled"];

interface Props {
  orders: Order[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function OrdersBoard({ orders, selectedId, onSelect }: Props) {
  const grouped = useMemo(() => {
    return COLUMNS.map((status) => ({
      status,
      meta: ORDER_STATUS_META[status],
      list: orders.filter((o) => o.status === status),
    }));
  }, [orders]);

  return (
    <div className="kanban">
      {grouped.map(({ status, meta, list }) => {
        const total = list.reduce((a, b) => a + b.total, 0);
        return (
          <div key={status} className="kcol">
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
    </div>
  );
}

interface CardProps {
  order: Order;
  selected: boolean;
  onSelect: (id: string) => void;
}

function Card({ order, selected, onSelect }: CardProps) {
  return (
    <div
      className={"kcard" + (selected ? " sel" : "")}
      onClick={() => onSelect(order.id)}
    >
      <div className="kc-top">
        <span className="oid">{order.id}</span>
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
        <span className={"pay-badge " + order.payType}>
          {order.payType === "cod" ? (
            <>
              <Icon.truck /> Contra entrega
            </>
          ) : (
            <>
              <Icon.check /> Pago confirmado
            </>
          )}
        </span>
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
  const today = "2026-05-12";
  const k = useMemo(
    () => ({
      todayCount: orders.filter((o) => o.dueIso === today).length,
      overdue: orders.filter((o) => o.overdue).length,
      revenue: orders
        .filter((o) => o.status !== "cancelled")
        .reduce((a, b) => a + b.total, 0),
      inProc: orders.filter((o) =>
        (["new", "preparing", "ready"] as OrderStatus[]).includes(o.status),
      ).length,
      shipping: orders.filter((o) => o.status === "shipping").length,
    }),
    [orders],
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
        <KPI label="Para hoy"    value={k.todayCount}             tone="accent" sub="3 en preparación" />
        <KPI label="Retrasadas"  value={k.overdue}                tone="red"    sub="Requieren atención" />
        <KPI label="En proceso"  value={k.inProc}                 tone="orange" sub="Nuevas + preparando + listas" />
        <KPI label="En tránsito" value={k.shipping}               tone="cyan"   sub="2 con guía activa" />
        <KPI label="Ingresos mes" value={fmtMoney(k.revenue, true)} tone="green" sub="↑ 12.4% vs. abril" />
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
