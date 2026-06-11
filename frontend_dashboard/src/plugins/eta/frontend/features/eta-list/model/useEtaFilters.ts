import { useMemo, useState } from "react";
import {
  isCodToday,
  type TrackedOrder,
} from "@plugins/eta/frontend/entities/tracked-order";

// Re-export: el predicado vive en la entity (lo comparte eta-cards); el
// banner COD del sidebar lo sigue importando desde acá.
export { isCodToday };

/**
 * `"all"` es el estado SIN filtro (no tiene chip en el sidebar — se llega
 * des-seleccionando el filtro activo con un segundo click, ver `EtaList`).
 */
export type EtaFilter =
  | "all"
  | "flag"
  | "cod"
  | "codToday"
  | "prep"
  | "ready"
  | "ship"
  | "delivered";

/**
 * FUENTE ÚNICA de los predicados de filtrado. El hook filtra con esto y los
 * chips del sidebar derivan sus contadores de esto — un chip nunca puede
 * mostrar un número distinto del que su click filtra (la clase de bug del
 * banner COD: contaba con un predicado y filtraba con otro).
 */
export const FILTER_PREDICATES: Record<
  Exclude<EtaFilter, "all">,
  (o: TrackedOrder) => boolean
> = {
  flag: (o) => o.needs,
  cod: (o) => o.payType === "cod",
  codToday: isCodToday,
  prep: (o) => o.current === "preparing",
  ready: (o) => o.current === "ready",
  ship: (o) => o.current === "shipping" || o.current === "out",
  delivered: (o) => o.current === "delivered",
};

export function useEtaFilters(orders: TrackedOrder[]) {
  const [filter, setFilter] = useState<EtaFilter>("all");

  const list = useMemo(() => {
    if (filter === "all") return orders;
    return orders.filter(FILTER_PREDICATES[filter]);
  }, [orders, filter]);

  return { filter, setFilter, list };
}

export const FILTER_LABELS: Record<EtaFilter, string> = {
  all: "Todos los pedidos rastreados",
  flag: "Pedidos que necesitan atención",
  cod: "Pedidos contra entrega",
  codToday: "Contra entrega hoy (en camino)",
  prep: "En preparación",
  ready: "Listos para envío",
  ship: "En camino",
  delivered: "Entregadas",
};
