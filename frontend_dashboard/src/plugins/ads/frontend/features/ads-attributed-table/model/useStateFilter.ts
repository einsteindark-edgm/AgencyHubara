import { useMemo, useState } from "react";

import {
  ADS_STATE_ORDER,
  type AdsState,
  type AttributedConversation,
} from "@/entities/ads-campaign";

export type AttributedStateFilter = "all" | AdsState;

/** Filtros disponibles en orden de presentación (incluye `"all"` al inicio). */
export const ATTRIBUTED_STATE_FILTERS: AttributedStateFilter[] = [
  "all",
  ...ADS_STATE_ORDER,
];

/** Slice de UI-state local de la tabla: filtro por estado + lista filtrada. */
export function useStateFilter(rows: AttributedConversation[]) {
  const [filter, setFilter] = useState<AttributedStateFilter>("all");

  const list = useMemo(() => {
    if (filter === "all") return rows;
    return rows.filter((r) => r.state === filter);
  }, [rows, filter]);

  return { filter, setFilter, list };
}
