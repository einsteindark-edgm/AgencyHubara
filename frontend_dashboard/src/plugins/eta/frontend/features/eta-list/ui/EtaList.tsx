/**
 * Sidebar de ETA: banner "Supervisando N pedidos", alerta COD para hoy y
 * filtros (Necesita atención / Contra entrega / por stage).
 */

import type { TrackedOrder } from "@plugins/eta/frontend/entities/tracked-order";
import { Icon } from "@/shared/ui";
import {
  FILTER_PREDICATES,
  isCodToday,
  type EtaFilter,
} from "../model/useEtaFilters";

interface Props {
  orders: TrackedOrder[];
  filter: EtaFilter;
  setFilter: (f: EtaFilter) => void;
}

export function EtaList({ orders, filter, setFilter }: Props) {
  const active = orders.length;
  // Mismo predicado que el filtro `codToday` — el click del banner muestra
  // EXACTAMENTE el conjunto que el banner cuenta (regresión: ruteaba a `cod`
  // genérico y aparecían COD que todavía no están en la calle).
  const codToday = orders.filter(isCodToday);
  const codTotal = codToday.reduce((a, b) => a + b.total, 0);

  // Sin chip "Todas": los chips son TOGGLE (click en el activo → des-filtra).
  // Los contadores se derivan de FILTER_PREDICATES — el mismo predicado que
  // ejecuta el filtro, así un chip nunca cuenta un conjunto distinto del que
  // muestra su click.
  const filters: {
    key: Exclude<EtaFilter, "all">;
    label: string;
    color?: string;
  }[] = [
    { key: "flag",      label: "Necesita atención", color: "#ff7269" },
    { key: "cod",       label: "Contra entrega",    color: "#ffb44a" },
    { key: "prep",      label: "En preparación",    color: "#ffb44a" },
    { key: "ready",     label: "Listas",            color: "#d68aff" },
    { key: "ship",      label: "En camino",         color: "#5fdcff" },
    { key: "delivered", label: "Entregadas",        color: "#5be07b" },
  ];
  const countFor = (key: Exclude<EtaFilter, "all">) =>
    orders.filter(FILTER_PREDICATES[key]).length;

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
        <div className="cod-alert" onClick={() => setFilter("codToday")}>
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
        {filters.map((f) => {
          const count = countFor(f.key);
          return (
            <div
              key={f.key}
              className={"of-row" + (filter === f.key ? " sel" : "")}
              onClick={() => setFilter(filter === f.key ? "all" : f.key)}
            >
              <span className="ofi" style={f.color ? { color: f.color } : undefined}>
                {f.key === "flag" ? (
                  <Icon.alert />
                ) : (
                  <span
                    className="st-dot"
                    style={{ background: f.color, width: 8, height: 8 }}
                  />
                )}
              </span>
              <span className="ofl">{f.label}</span>
              <span
                className={
                  "ofn" + (f.key === "flag" && count > 0 ? " accent-red" : "")
                }
              >
                {count}
              </span>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
