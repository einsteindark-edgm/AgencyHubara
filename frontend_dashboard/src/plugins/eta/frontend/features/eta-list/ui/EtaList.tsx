/**
 * Sidebar de ETA: banner "Supervisando N pedidos", alerta COD para hoy y
 * filtros (Necesita atención / Contra entrega / por stage).
 */

import type { TrackedOrder } from "@plugins/eta/frontend/entities/tracked-order";
import { Icon } from "@/shared/ui";
import { type EtaFilter } from "../model/useEtaFilters";

interface Props {
  orders: TrackedOrder[];
  filter: EtaFilter;
  setFilter: (f: EtaFilter) => void;
}

export function EtaList({ orders, filter, setFilter }: Props) {
  const flagged = orders.filter((o) => o.needs).length;
  const active = orders.length;
  const codToday = orders.filter(
    (o) =>
      o.payType === "cod" && (o.current === "shipping" || o.current === "out"),
  );
  const codTotal = codToday.reduce((a, b) => a + b.total, 0);

  const filters: {
    key: EtaFilter;
    label: string;
    count: number;
    color?: string;
  }[] = [
    { key: "all",   label: "Todas",            count: active },
    { key: "flag",  label: "Necesita atención", count: flagged, color: "#ff7269" },
    { key: "cod",   label: "Contra entrega",    count: orders.filter((o) => o.payType === "cod").length, color: "#ffb44a" },
    { key: "prep",  label: "En preparación",    count: orders.filter((o) => o.current === "preparing").length, color: "#ffb44a" },
    { key: "ready", label: "Listas",            count: orders.filter((o) => o.current === "ready").length, color: "#d68aff" },
    { key: "ship",  label: "En camino",         count: orders.filter((o) => o.current === "shipping" || o.current === "out").length, color: "#5fdcff" },
  ];

  return (
    <aside className="sidebar">
      <div className="sb-head">
        <h2>ETA agent</h2>
        <span className="eta-live">
          <span className="d" />
          Activo
        </span>
      </div>

      <div className="eta-banner">
        <span className="eb-i"><Icon.bot /></span>
        <div>
          <div className="eb-t">Supervisando {active} pedidos</div>
          <div className="eb-s">
            Activado automáticamente al pasar a <b>En preparación</b>
          </div>
        </div>
      </div>

      {codToday.length > 0 && (
        <div className="cod-alert" onClick={() => setFilter("cod")}>
          <span className="ca-i"><Icon.alert /></span>
          <div>
            <div className="ca-t">{codToday.length} contra entrega hoy</div>
            <div className="ca-s">
              A cobrar al recibir:{" "}
              <b>$ {codTotal.toLocaleString("es-CO")}</b>
            </div>
          </div>
          <span className="ca-chev">›</span>
        </div>
      )}

      <div className="sb-section">
        <div className="sb-section-h"><span>Filtros</span></div>
        {filters.map((f) => (
          <div
            key={f.key}
            className={"of-row" + (filter === f.key ? " sel" : "")}
            onClick={() => setFilter(f.key)}
          >
            <span className="ofi" style={f.color ? { color: f.color } : undefined}>
              {f.key === "flag" ? (
                <Icon.alert />
              ) : f.color ? (
                <span
                  className="st-dot"
                  style={{ background: f.color, width: 8, height: 8 }}
                />
              ) : (
                <Icon.box />
              )}
            </span>
            <span className="ofl">{f.label}</span>
            <span
              className={
                "ofn" + (f.key === "flag" && f.count > 0 ? " accent-red" : "")
              }
            >
              {f.count}
            </span>
          </div>
        ))}
      </div>
    </aside>
  );
}
