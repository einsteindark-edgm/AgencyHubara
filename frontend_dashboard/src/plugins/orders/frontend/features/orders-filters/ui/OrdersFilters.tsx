/**
 * Sidebar de Órdenes: vistas (Todas/Hoy/Retrasadas/Mañana/Semana/En camino) +
 * Modalidad de pago (Anticipado / Contra entrega) + Canales + Etiquetas.
 *
 * IMPORTANT: el filtro filtra por `payType` (modalidad — cómo se paga), NO
 * por `payStatus` (estado del pago — si está capturado). "Anticipado" = no es
 * COD; puede estar pagado o pendiente. El badge de estado del pago vive en
 * la card del kanban.
 *
 * Recibe el estado de filtros por prop. Owner del estado: la página, vía
 * `useOrderFilters` reusado por ambas features (filters + board).
 */

import { Icon, MacButton } from "@/shared/ui";
import type { Order } from "@/entities/order";
import type { PayTypeFilter, ViewFilter } from "../model/useOrderFilters";

const CHANNELS = ["WhatsApp", "Instagram", "Web", "Tienda", "Mercado Libre"];

interface Props {
  view: ViewFilter;
  setView: (v: ViewFilter) => void;
  payType: PayTypeFilter;
  setPayType: (p: PayTypeFilter) => void;
  orders: Order[];
}

export function OrdersFilters({ view, setView, payType, setPayType, orders }: Props) {
  const today = new Date().toISOString().slice(0, 10);
  const tomorrow = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);
  // Esta semana = próximos 7 días incluyendo hoy.
  const weekIsos = new Set<string>();
  for (let i = 0; i < 7; i++) {
    weekIsos.add(new Date(Date.now() + i * 86_400_000).toISOString().slice(0, 10));
  }
  // Bug fix 2026-05-26: counters de las vistas excluyen órdenes canceladas.
  // Las canceladas viven en la columna "Cancelada" del kanban pero NO se
  // suman a las métricas operacionales (sería ruido — "5 órdenes para hoy"
  // incluyendo 3 canceladas no es accionable).
  const active = orders.filter((o) => o.status !== "cancelled");
  const weekCount = active.filter((o) => !!o.dueIso && weekIsos.has(o.dueIso)).length;

  const views: {
    key: ViewFilter; label: string; count: number;
    icon: React.ReactNode; accent?: boolean; color?: string;
  }[] = [
    { key: "all",         label: "Todas",        count: active.length,                                                       icon: <Icon.box /> },
    { key: "unscheduled", label: "Sin agendar",  count: active.filter((o) => !o.dueIso).length,                              icon: <Icon.cal />, color: "#ffb44a" },
    { key: "today",       label: "Para hoy",     count: active.filter((o) => !!o.dueIso && o.dueIso === today).length,       icon: <Icon.clock />, accent: true },
    { key: "overdue",     label: "Retrasadas",   count: active.filter((o) => o.overdue).length,                              icon: <Icon.alert />, color: "#ff7269" },
    { key: "tomorrow",    label: "Mañana",       count: active.filter((o) => !!o.dueIso && o.dueIso === tomorrow).length,    icon: <Icon.cal /> },
    { key: "week",        label: "Esta semana",  count: weekCount,                                                           icon: <Icon.cal /> },
    { key: "inprocess",   label: "En proceso",   count: active.filter((o) => o.status === "preparing" || o.status === "ready").length, icon: <Icon.pkg /> },
    { key: "ship",        label: "En camino",    count: active.filter((o) => o.status === "shipping").length,                icon: <Icon.truck /> },
  ];

  const payTypes: {
    key: PayTypeFilter; label: string; count: number; dot: string;
  }[] = [
    { key: "all",       label: "Todas las modalidades", count: active.length,                                          dot: "rgba(255,255,255,0.25)" },
    { key: "confirmed", label: "Anticipado",            count: active.filter((o) => o.payType === "confirmed").length, dot: "#5be07b" },
    { key: "cod",       label: "Contra entrega",        count: active.filter((o) => o.payType === "cod").length,       dot: "#ffb44a" },
  ];

  return (
    <aside className="sidebar">
      <div className="sb-head">
        <h2>Órdenes</h2>
        <MacButton primary sm><Icon.plus /> Nueva</MacButton>
      </div>

      <div className="sb-search">
        <Icon.search />
        <input placeholder="Buscar # orden o cliente…" />
      </div>

      <div className="sb-section">
        <div className="sb-section-h"><span>Vistas</span></div>
        {views.map((v) => (
          <div
            key={v.key}
            className={"of-row" + (view === v.key ? " sel" : "")}
            onClick={() => setView(v.key)}
          >
            <span className="ofi" style={v.color ? { color: v.color } : undefined}>
              {v.icon}
            </span>
            <span className="ofl">{v.label}</span>
            <span className={"ofn" + (v.accent ? " accent" : "")}>{v.count}</span>
          </div>
        ))}
      </div>

      <div className="sb-section">
        <div className="sb-section-h"><span>Modalidad de pago</span></div>
        {payTypes.map((p) => (
          <div
            key={p.key}
            className={"of-row st-row" + (payType === p.key ? " sel" : "")}
            onClick={() => setPayType(p.key)}
          >
            <span className="st-dot" style={{ background: p.dot }} />
            <span className="ofl">{p.label}</span>
            <span className="ofn">{p.count}</span>
          </div>
        ))}
      </div>

      <div className="sb-section">
        <div className="sb-section-h"><span>Canal</span></div>
        {CHANNELS.map((c) => (
          <div key={c} className="of-row st-row">
            <span className="st-dot" style={{ background: "rgba(255,255,255,0.25)" }} />
            <span className="ofl">{c}</span>
            <span className="ofn">
              {active.filter((o) => o.channel === c).length || "—"}
            </span>
          </div>
        ))}
      </div>

      <div className="sb-section">
        <div className="sb-section-h"><span>Etiquetas</span></div>
        <div
          style={{ display: "flex", flexWrap: "wrap", gap: 5, padding: "4px 12px 8px" }}
        >
          {["VIP", "Repetido", "B2B", "Mayorista", "Frágil", "Express", "Recurrente"].map(
            (t) => (
              <span key={t} className="tg-chip">
                {t}
              </span>
            ),
          )}
        </div>
      </div>

      <div className="sb-foot">
        {/* Footer: solo cuenta órdenes activas (no canceladas). Si querés ver
            las canceladas, mirá la columna "Cancelada" en el kanban. */}
        <span>{active.length} órdenes activas</span>
        <button className="ico-btn" title="Exportar">
          <Icon.download />
        </button>
      </div>
    </aside>
  );
}
