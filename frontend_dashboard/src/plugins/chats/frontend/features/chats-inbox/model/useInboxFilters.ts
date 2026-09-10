/**
 * Filtros del inbox: tag activo (incluyendo "Humano"), rango de fechas del
 * calendario, y agrupación en secciones (fijadas / hoy / anteriores).
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
import { formatDayLabelEs, todayBogotaIso } from "@/shared/lib";

export type InboxFilter = "Humano" | "Todas" | "Interesado" | "Pendiente" | "Cliente" | "Remarketing" | "Frío";

/** Rango cerrado de días calendario (YYYY-MM-DD, Bogotá). `to: null` = rango a
 *  medio elegir en el calendario: se comporta como un día suelto. */
export interface DateRange {
  from: string | null;
  to: string | null;
}

export interface InboxSection {
  key: "pinned" | "waiting" | "today" | "earlier" | "results";
  title: string;
  items: ChatInboxItem[];
}

const NO_RANGE: DateRange = { from: null, to: null };

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

/** ¿El día `dayIso` cae dentro del rango? Sin `from` el rango está inactivo. */
function inRange(dayIso: string, range: DateRange): boolean {
  if (!range.from) return true;
  if (!dayIso) return false; // sin fecha conocida no puede afirmarse que entra
  const to = range.to ?? range.from;
  return dayIso >= range.from && dayIso <= to;
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
  const [dateRange, setDateRange] = useState<DateRange>(NO_RANGE);

  const clearDateRange = useCallback(() => setDateRange(NO_RANGE), []);
  const hasDateRange = dateRange.from !== null;

  /** Chats que pasan SOLO el filtro de fecha. Es la base tanto de los
   *  contadores de los pills como del filtrado final: si los contadores
   *  ignoraran el rango, dirían "12" y la lista mostraría 2. */
  const inDateRange = useMemo(
    () => chats.filter((c) => inRange(c.dayIso, dateRange)),
    [chats, dateRange],
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
    const humanCount = inDateRange.filter((c) => c.human).length;
    const byTag = (key: InboxFilter) =>
      inDateRange.filter((c) => TAG_TO_FILTER[c.tag] === key).length;
    return [
      { key: "Humano",      count: humanCount,          color: "var(--color-danger)",        priority: true },
      { key: "Todas",       count: inDateRange.length,  color: "var(--fg-soft)" },
      { key: "Interesado",  count: byTag("Interesado"), color: "var(--color-info)" },
      { key: "Pendiente",   count: byTag("Pendiente"),  color: "var(--color-warn)" },
      { key: "Cliente",     count: byTag("Cliente"),    color: "var(--color-ok)" },
      { key: "Remarketing", count: byTag("Remarketing"),color: "var(--color-violet)" },
      { key: "Frío",        count: byTag("Frío"),       color: "rgba(235,235,235,0.55)" },
    ];
  }, [inDateRange]);

  const filtered = useMemo(() => {
    if (activeFilter === "Humano") return inDateRange.filter((c) => c.human);
    if (activeFilter === "Todas") return inDateRange;
    return inDateRange.filter((c) => TAG_TO_FILTER[c.tag] === activeFilter);
  }, [inDateRange, activeFilter]);

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
      out.push({ key: "results", title: describeRange(dateRange, today), items: rest });
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
    dateRange,
    setDateRange,
    clearDateRange,
    hasDateRange,
    /** Texto corto del rango para el botón que abre el calendario. */
    dateRangeLabel: hasDateRange ? describeRange(dateRange, today) : "Todas las fechas",
    activeDays,
    sections,
    filtered,
    today,
  };
}

// Sólo el mes: pedirle a es-CO `{day, month:"short"}` devuelve "8 de sept."
// — con preposición y punto. Se compone a mano.
const SHORT_MONTH_FMT = new Intl.DateTimeFormat("es-CO", {
  month: "short",
  timeZone: "UTC",
});

/** "8 sep" — compacto, sin preposición ni punto abreviativo. */
function shortDay(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  const month = SHORT_MONTH_FMT.format(new Date(Date.UTC(y, m - 1, d)))
    .replace(/\.$/, "")
    .replace(/^sept$/, "sep");
  return `${d} ${month}`;
}

/**
 * Texto del rango. Un día suelto va en lenguaje humano ("Hoy", "Ayer"); un
 * rango va COMPACTO porque tiene que entrar en los 280px de la sidebar y en el
 * encabezado de la sección — la fecha larga de los dos extremos ("8 de
 * septiembre de 2026 – 9 de septiembre de 2026") se corta con elipsis y el
 * operador deja de ver qué filtro tiene puesto.
 *
 * El año se escribe una sola vez cuando ambos extremos lo comparten.
 */
function describeRange(range: DateRange, today: string): string {
  if (!range.from) return "Todas las fechas";
  const to = range.to ?? range.from;
  if (range.from === to) return formatDayLabelEs(range.from, today);

  const [fromYear, toYear] = [range.from.slice(0, 4), to.slice(0, 4)];
  if (fromYear !== toYear) {
    return `${shortDay(range.from)} ${fromYear} – ${shortDay(to)} ${toYear}`;
  }
  // Mismo mes: no repetirlo ("8 – 9 sep 2026").
  const sameMonth = range.from.slice(0, 7) === to.slice(0, 7);
  const left = sameMonth ? String(Number(range.from.slice(8))) : shortDay(range.from);
  return `${left} – ${shortDay(to)} ${toYear}`;
}
