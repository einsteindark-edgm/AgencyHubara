import { useMemo, useState } from "react";
import type { TrackedOrder } from "@plugins/eta/frontend/entities/tracked-order";

export type EtaFilter = "all" | "flag" | "cod" | "prep" | "ready" | "ship";

export function useEtaFilters(orders: TrackedOrder[]) {
  const [filter, setFilter] = useState<EtaFilter>("all");

  const list = useMemo(() => {
    return orders.filter((o) => {
      if (filter === "flag")  return o.needs;
      if (filter === "cod")   return o.payType === "cod";
      if (filter === "prep")  return o.current === "preparing";
      if (filter === "ready") return o.current === "ready";
      if (filter === "ship")  return o.current === "shipping" || o.current === "out";
      return true;
    });
  }, [orders, filter]);

  return { filter, setFilter, list };
}

export const FILTER_LABELS: Record<EtaFilter, string> = {
  all: "Todos los pedidos rastreados",
  flag: "Pedidos que necesitan atención",
  cod: "Pedidos contra entrega",
  prep: "En preparación",
  ready: "Listos para envío",
  ship: "En camino",
};
