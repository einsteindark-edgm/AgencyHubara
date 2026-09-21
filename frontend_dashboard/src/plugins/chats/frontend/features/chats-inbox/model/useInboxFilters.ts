/**
 * Filtros del inbox: búsqueda, tag activo (incluyendo "Humano"), rango de
 * fechas del calendario, y agrupación en secciones (fijadas / hoy / anteriores).
 *
 * Todo lo temporal está anclado a **America/Bogota**. El día calendario ya
 * viene resuelto en `ChatInboxItem.dayIso` (lo pone el adaptador de la entity),
 * así que acá se compara string contra string — sin aritmética de zonas.
 *
 * Regresión que cierra este módulo: la sección "Hoy" era `rest.slice(0, 6)`,
 * o sea los primeros seis chats de la lista. No miraba una sola fecha, así que
 * con siete conversaciones en la bandeja ya mostraba chats de semanas atrás
 * bajo "Hoy".
 */

import { useCallback, useMemo, useState } from "react";
import type { ChatInboxItem } from "@plugins/chats/frontend/entities/chat";
import {
  NO_DATE_RANGE,
  describeDateRange,
  isInDateRange,
  matchesSearch,
  todayBogotaIso,
  type DateRange,
} from "@/shared/lib";

export type { DateRange };

export type InboxFilter =
  | "Humano" | "Todas" | "Interesado" | "Pendiente" | "Cliente" | "Remarketing" | "Frío"
  | "Sin respuesta";

export interface InboxSection {
  key: "pinned" | "waiting" | "today" | "earlier" | "results";
  title: string;
  items: ChatInboxItem[];
}

const TAG_TO_FILTER: Record<string, InboxFilter> = {
  INTERESADO: "Interesado",
  PENDIENTE: "Pendiente",
  CLIENTE: "Cliente",
  REMARKETING: "Remarketing",
  FRÍO: "Frío",
  SIN_RESPUESTA: "Sin respuesta",
  HUMANO: "Humano",
};

export interface InboxFilterMeta {
  key: InboxFilter;
  count: number;
  color: string;
  priority?: boolean;
}

/** Lo que el operador tiene a mano para buscar: el teléfono, el diagnóstico
 *  de la fila y el número de pedido ("#31"). */
function matchesChat(c: ChatInboxItem, query: string): boolean {
  return matchesSearch(query, [c.name, c.snippet, c.order?.label]);
}

/** Más reciente primero. */
function byRecency(a: ChatInboxItem, b: ChatInboxItem): number {
  return b.timestamp - a.timestamp;
}

interface Options {
  /** Día "hoy" en Bogotá. Inyectable para tests; en la app lo resuelve el reloj
   *  en cada render (nunca memoizado: si se congelara, la bandeja seguiría
   *  diciendo "Hoy" al día siguiente). */
  today?: string;
}

export function useInboxFilters(chats: ChatInboxItem[], options: Options = {}) {
  const today = options.today ?? todayBogotaIso();
  const [activeFilter, setActiveFilter] = useState<InboxFilter>("Humano");
  const [dateRange, setDateRange] = useState<DateRange>(NO_DATE_RANGE);
  const [query, setQuery] = useState("");

  const clearDateRange = useCallback(() => setDateRange(NO_DATE_RANGE), []);
  const clearQuery = useCallback(() => setQuery(""), []);
  const hasDateRange = dateRange.from !== null;

  /** Chats que pasan el rango de fechas y la búsqueda — NO el tag. Es la base
   *  tanto de los contadores de los pills como del filtrado final: si los
   *  contadores ignoraran el rango o la búsqueda, dirían "12" y la lista
   *  mostraría 2. Con la búsqueda además dicen en qué pill está lo buscado. */
  const inScope = useMemo(
    () => chats.filter((c) => isInDateRange(c.dayIso, dateRange) && matchesChat(c, query)),
    [chats, dateRange, query],
  );

  /** Días con al menos una conversación — el calendario los marca con un punto
   *  para que el operador no cace días vacíos a ciegas. Se calcula sobre la
   *  bandeja COMPLETA (no sobre lo filtrado): si dependiera del rango activo,
   *  al elegir un día se apagarían todos los demás. */
  const activeDays = useMemo(() => {
    const days = new Set<string>();
    for (const c of chats) if (c.dayIso) days.add(c.dayIso);
    return days;
  }, [chats]);

  const filters: InboxFilterMeta[] = useMemo(() => {
    const humanCount = inScope.filter((c) => c.human).length;
    const byTag = (key: InboxFilter) =>
      inScope.filter((c) => TAG_TO_FILTER[c.tag] === key).length;
    return [
      { key: "Humano",      count: humanCount,          color: "var(--color-danger)",        priority: true },
      { key: "Todas",       count: inScope.length,      color: "var(--fg-soft)" },
      { key: "Interesado",  count: byTag("Interesado"), color: "var(--color-info)" },
      { key: "Pendiente",   count: byTag("Pendiente"),  color: "var(--color-warn)" },
      { key: "Cliente",     count: byTag("Cliente"),    color: "var(--color-ok)" },
      { key: "Remarketing", count: byTag("Remarketing"),color: "var(--color-violet)" },
      { key: "Frío",        count: byTag("Frío"),       color: "rgba(235,235,235,0.55)" },
      // Agotó la escalera de reactivación sin contestar (tag SIN_RESPUESTA).
      { key: "Sin respuesta", count: byTag("Sin respuesta"), color: "var(--fg-muted)" },
    ];
  }, [inScope]);

  const filtered = useMemo(() => {
    if (activeFilter === "Humano") return inScope.filter((c) => c.human);
    if (activeFilter === "Todas") return inScope;
    return inScope.filter((c) => TAG_TO_FILTER[c.tag] === activeFilter);
  }, [inScope, activeFilter]);

  const sections: InboxSection[] = useMemo(() => {
    const out: InboxSection[] = [];
    const pinned = filtered.filter((c) => c.pinned).sort(byRecency);
    const rest = filtered.filter((c) => !c.pinned).sort(byRecency);

    if (pinned.length > 0) {
      out.push({ key: "pinned", title: "Fijadas", items: pinned });
    }
    if (rest.length === 0) return out;

    // Con un rango elegido, partir en "Hoy / Anteriores" es ruido: el operador
    // ya dijo qué ventana quiere ver. Una sola sección rotulada con el rango.
    if (hasDateRange) {
      out.push({ key: "results", title: describeDateRange(dateRange, today), items: rest });
      return out;
    }
    // El filtro Humano es una COLA, no un archivo: todas esperan respuesta,
    // partirlas por día esconde justamente las que llevan más tiempo colgadas.
    if (activeFilter === "Humano") {
      out.push({ key: "waiting", title: "Esperando respuesta", items: rest });
      return out;
    }

    const todayItems = rest.filter((c) => c.dayIso === today);
    const earlier = rest.filter((c) => c.dayIso !== today);
    if (todayItems.length > 0) {
      out.push({ key: "today", title: "Hoy", items: todayItems });
    }
    if (earlier.length > 0) {
      out.push({ key: "earlier", title: "Anteriores", items: earlier });
    }
    return out;
  }, [filtered, hasDateRange, dateRange, activeFilter, today]);

  return {
    activeFilter,
    setActiveFilter,
    filters,
    query,
    setQuery,
    clearQuery,
    dateRange,
    setDateRange,
    clearDateRange,
    hasDateRange,
    /** Texto corto del rango para el botón que abre el calendario. */
    dateRangeLabel: hasDateRange ? describeDateRange(dateRange, today) : "Todas las fechas",
    activeDays,
    sections,
    filtered,
    today,
  };
}
