import { useMemo, useState } from "react";
import type { TrackedOrder } from "@plugins/eta/frontend/entities/tracked-order";

export type EtaFilter =
  | "all"
  | "flag"
  | "cod"
  | "codToday"
  | "prep"
  | "ready"
  | "ship";

/**
 * "Contra entrega hoy" = COD que ya está EN LA CALLE (shipping/out): la plata
 * se cobra al entregar, así que es lo que el repartidor recauda hoy. Es el
 * MISMO predicado que usa el banner `cod-alert` del sidebar — el click del
 * banner debe mostrar exactamente el conjunto que el banner contó (antes
 * ruteaba al filtro `cod` genérico y aparecían también los COD en
 * preparación/listos, que aún no se cobran).
 */
export function isCodToday(o: TrackedOrder): boolean {
  return o.payType === "cod" && (o.current === "shipping" || o.current === "out");
}

export function useEtaFilters(orders: TrackedOrder[]) {
  const [filter, setFilter] = useState<EtaFilter>("all");

  const list = useMemo(() => {
    return orders.filter((o) => {
      if (filter === "flag")     return o.needs;
      if (filter === "cod")      return o.payType === "cod";
      if (filter === "codToday") return isCodToday(o);
      if (filter === "prep")     return o.current === "preparing";
      if (filter === "ready")    return o.current === "ready";
      if (filter === "ship")     return o.current === "shipping" || o.current === "out";
      return true;
    });
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
};
