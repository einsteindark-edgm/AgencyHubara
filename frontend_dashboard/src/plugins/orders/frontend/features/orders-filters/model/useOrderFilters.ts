/**
 * Filtros y agrupaciones de la vista Órdenes: la búsqueda, el rango de fechas
 * del calendario (el mismo de Chats) y la vista + modalidad del panel
 * izquierdo (no hay agrupar/orden/cambio-a-tabla — eliminados por feedback del
 * usuario en la iteración del prototipo).
 *
 * Tres alcances, cada uno para un consumidor:
 *   - `scoped`   = búsqueda + rango → contadores de la sidebar.
 *   - `kpiScope` = scoped + modalidad → KPIs del tablero (NO la vista: son el
 *     resumen del período, no de la columna que se está mirando).
 *   - `filtered` = kpiScope + vista → kanban.
 *
 * Los pedidos de prueba quedan en `filtered` (el kanban los muestra con su
 * etiqueta) pero NO en `scoped` ni `kpiScope`: no cuentan en ningún número.
 *
 * Las vistas por fecha cortan el día en hora COLOMBIA, igual que los
 * contadores de la sidebar (`OrdersFilters`): `dueIso` es un día calendario
 * que el operador eligió a mano, no un instante.
 */

import { useCallback, useMemo, useState } from "react";
import {
  NO_DATE_RANGE,
  addDaysBogotaIso,
  describeDateRange,
  isInDateRange,
  matchesSearch,
  nextDaysBogotaIsoSet,
  todayBogotaIso,
  type DateRange,
} from "@/shared/lib";
import {
  countsInStats,
  orderDayIso,
  type Order,
  type PayType,
} from "@plugins/orders/frontend/entities/order";

export type ViewFilter =
  | "all"
  | "unscheduled"
  | "today"
  | "overdue"
  | "tomorrow"
  | "week"
  | "inprocess"
  | "ship";
export type PayTypeFilter = "all" | PayType;

/** Lo que promete el placeholder ("# orden o cliente") + el teléfono, que es
 *  como el cliente se identifica cuando escribe o llama. */
function matchesOrder(o: Order, query: string): boolean {
  return matchesSearch(query, [o.id, o.customer, o.phone]);
}

export function useOrderFilters(orders: Order[]) {
  const [query, setQuery] = useState("");
  const [view, setView] = useState<ViewFilter>("all");
  const [payType, setPayType] = useState<PayTypeFilter>("all");
  const [dateRange, setDateRange] = useState<DateRange>(NO_DATE_RANGE);
  const clearDateRange = useCallback(() => setDateRange(NO_DATE_RANGE), []);
  const hasDateRange = dateRange.from !== null;

  /** Órdenes que coinciden con la búsqueda y el rango — sin vista ni
   *  modalidad. Es la base de los contadores de la sidebar: así dicen en qué
   *  vista quedó lo buscado/el período en vez de seguir contando todo. */
  const scopedWithTests = useMemo(
    () =>
      orders.filter(
        (o) => matchesOrder(o, query) && isInDateRange(orderDayIso(o), dateRange),
      ),
    [orders, query, dateRange],
  );

  const boardScope = useMemo(
    () =>
      payType === "all"
        ? scopedWithTests
        : scopedWithTests.filter((o) => o.payType === payType),
    [scopedWithTests, payType],
  );

  const scoped = useMemo(() => scopedWithTests.filter(countsInStats), [scopedWithTests]);
  const kpiScope = useMemo(() => boardScope.filter(countsInStats), [boardScope]);

  /** Días con al menos una orden — el calendario los marca con un punto. Sobre
   *  TODAS las órdenes: si dependiera del rango, al elegir un día se apagarían
   *  los demás. */
  const activeDays = useMemo(() => {
    const days = new Set<string>();
    for (const o of orders) {
      const day = orderDayIso(o);
      if (day) days.add(day);
    }
    return days;
  }, [orders]);

  // Día COLOMBIANO, el mismo corte que los contadores de la sidebar. En UTC
  // la frontera caía a las 19:00 locales: desde esa hora "Para hoy" llenaba el
  // tablero con las entregas de mañana mientras su contador contaba las de hoy.
  // Se lee en cada render y entra en las deps: congelado dentro del memo, pasada
  // la medianoche el filtro seguiría en el día anterior.
  const today = todayBogotaIso();
  const tomorrow = addDaysBogotaIso(1);

  const filtered = useMemo(() => {
    // Esta semana = próximos 7 días incluyendo hoy. Un Set no sirve de dep:
    // se arma acá y se rehace cada vez que cambia `today`.
    const weekIsos = nextDaysBogotaIsoSet(7);
    return boardScope.filter((o) => {
      // "No agendadas" = órdenes sin fecha de entrega asignada — típicamente
      // las que también viven en la columna "Nueva" del kanban porque el
      // operador todavía no las agendó.
      if (view === "unscheduled") return !o.dueIso;
      // Filtros de fecha: comparamos contra `dueIso` (fecha de entrega real,
      // no fecha de creación). Si no tiene dueIso, NO matchea ningún
      // filtro de fecha (porque no está agendada).
      if (view === "today")    return !!o.dueIso && o.dueIso === today;
      if (view === "overdue")  return o.overdue === true;
      if (view === "tomorrow") return !!o.dueIso && o.dueIso === tomorrow;
      if (view === "week")     return !!o.dueIso && weekIsos.has(o.dueIso);
      // "En proceso" = preparing + ready (operativas, no terminales ni new).
      if (view === "inprocess") return o.status === "preparing" || o.status === "ready";
      if (view === "ship")     return o.status === "shipping";
      return true;
    });
  }, [boardScope, view, today, tomorrow]);

  return {
    query,
    setQuery,
    view,
    setView,
    payType,
    setPayType,
    dateRange,
    setDateRange,
    clearDateRange,
    /** Texto corto del rango para el disparador del calendario y los KPIs. */
    dateRangeLabel: hasDateRange ? describeDateRange(dateRange, today) : "Todas las fechas",
    activeDays,
    today,
    scoped,
    kpiScope,
    filtered,
  };
}

export function filterLabel(view: ViewFilter): string {
  const map: Record<ViewFilter, string> = {
    all: "Todas las órdenes",
    unscheduled: "Órdenes sin agendar",
    today: "Órdenes para hoy",
    overdue: "Órdenes retrasadas",
    tomorrow: "Órdenes para mañana",
    week: "Órdenes de la semana",
    inprocess: "Órdenes en proceso",
    ship: "Órdenes en camino",
  };
  return map[view];
}
