/**
 * Filtros del inbox: tag activo (incluyendo "Humano") + agrupación
 * (fijadas / hoy o esperando / anteriores).
 */

import { useMemo, useState } from "react";
import type { ChatInboxItem } from "@plugins/chats/frontend/entities/chat";

export type InboxFilter = "Humano" | "Todas" | "Interesado" | "Pendiente" | "Cliente" | "Remarketing" | "Frío";

const TAG_TO_FILTER: Record<string, InboxFilter> = {
  INTERESADO: "Interesado",
  PENDIENTE: "Pendiente",
  CLIENTE: "Cliente",
  REMARKETING: "Remarketing",
  FRÍO: "Frío",
  HUMANO: "Humano",
};

export interface InboxFilterMeta {
  key: InboxFilter;
  count: number;
  color: string;
  priority?: boolean;
}

export function useInboxFilters(chats: ChatInboxItem[]) {
  const [activeFilter, setActiveFilter] = useState<InboxFilter>("Humano");

  const filters: InboxFilterMeta[] = useMemo(() => {
    const humanCount = chats.filter((c) => c.human).length;
    const byTag = (key: InboxFilter) =>
      chats.filter((c) => TAG_TO_FILTER[c.tag] === key).length;
    return [
      { key: "Humano",      count: humanCount,         color: "var(--color-danger)",                       priority: true },
      { key: "Todas",       count: chats.length,        color: "var(--fg-soft)" },
      { key: "Interesado",  count: byTag("Interesado"), color: "var(--color-info)" },
      { key: "Pendiente",   count: byTag("Pendiente"),  color: "var(--color-warn)" },
      { key: "Cliente",     count: byTag("Cliente"),    color: "var(--color-ok)" },
      { key: "Remarketing", count: byTag("Remarketing"),color: "var(--color-violet)" },
      { key: "Frío",        count: byTag("Frío"),       color: "rgba(235,235,235,0.55)" },
    ];
  }, [chats]);

  const filtered = useMemo(() => {
    if (activeFilter === "Humano") return chats.filter((c) => c.human);
    if (activeFilter === "Todas") return chats;
    return chats.filter((c) => TAG_TO_FILTER[c.tag] === activeFilter);
  }, [chats, activeFilter]);

  const { pinned, today, earlier } = useMemo(() => {
    const pinnedList = filtered.filter((c) => c.pinned);
    const rest = filtered.filter((c) => !c.pinned);
    return {
      pinned: pinnedList,
      today: rest.slice(0, 6),
      earlier: rest.slice(6),
    };
  }, [filtered]);

  return {
    activeFilter,
    setActiveFilter,
    filters,
    pinned,
    today,
    earlier,
    filtered,
  };
}
