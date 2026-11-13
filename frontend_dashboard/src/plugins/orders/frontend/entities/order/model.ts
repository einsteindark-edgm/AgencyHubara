/**
 * Tipos del dominio "orden". Los datos vienen del backend en
 * `/api/orders/orders` (Medusa v2 v read-side), pero la UI del kanban
 * necesita un shape específico — este modelo es el contrato canónico
 * que consumen los componentes (`OrdersBoard`, `OrdersInspector`, etc.).
 *
 * Re-exporta tipos del backend (Zod-inferred en `contracts.ts`). Todo lo
 * dependiente del reloj (`overdue`) viene calculado del backend; los labels
 * relativos ("hoy/mañana") se derivan en render, nunca en el mapper
 * (auditoría 2026-06-10, F0.5).
 */

import { isInDateRange, type DateRange } from "@/shared/lib";
import type {
  OrderDetail,
  OrderItemDetail,
  OrderAddress,
  OrderLines,
  OrderSummary,
  OrderTimelineEvent,
} from "./contracts";

export type OrderStatus =
  | "new"
  | "preparing"
  | "ready"
  | "shipping"
  | "delivered"
  | "delayed"
  | "cancelled";

export type PayStatus = "paid" | "partial" | "pending" | "refund";
export type PayType = "cod" | "confirmed";

/**
 * Shape que consume `OrdersBoard` y `OrdersInspector`. Mantiene la forma
 * histórica del prototipo (camelCase + algunas derivaciones UI) — los
 * mappers en `api.ts` convierten `OrderSummary` del backend (snake_case)
 * a este shape para que las features no tengan que cambiar todavía.
 */
export interface Order {
  id: string;
  customer: string;
  short: string;
  color: "a" | "b" | "c" | "d" | "e" | "f";
  phone: string;
  city: string;
  channel: string;
  status: OrderStatus;
  payStatus: PayStatus;
  payType: PayType;
  items: number;
  /** Total vigente: pedido + envío (estimado, o el real si ya se fijó). */
  total: number;
  /** Envío incluido en `total` (0 = desconocido). */
  shipping?: number;
  /** `true` cuando `shipping` es el valor real que fijó el operador al
   *  marcar "en camino"; `false` = tarifa mínima estimada del registro. */
  shippingConfirmed?: boolean;
  /** Envío gratis (desde N cajas de una campaña): el cliente no paga envío. */
  freeShipping?: boolean;
  /** Lo que la tienda paga por el envío de un pedido con envío gratis (el
   *  valor real del despacho); `null` = todavía no se escribió. */
  storeShippingCost?: number | null;
  dueIso: string;
  dueTime: string;
  overdue?: boolean;
  pieces: number;
  agent: string;
  priority: "alta" | "normal" | "baja";
  // Nuevos: meta del backend para que la UI sepa cuándo pintar markers.
  isDraft: boolean;
  /** Marcado "prueba" en el inspector: sigue en el tablero pero NO cuenta en
   *  ningún número (ver `countsInStats`). */
  isTest?: boolean;
  isDueEstimated: boolean; // true cuando dueIso es estimate (created+1d)
  /** Día de creación YYYY-MM-DD en America/Bogota ("" si el backend no lo
   *  mandó). Ubica en el calendario a las órdenes sin entrega agendada. */
  createdIso: string;
}

/**
 * El día con el que una orden cae en el filtro por fecha y en "Ingresos": la
 * ENTREGA agendada; si no está agendada, el día en que se creó. Con solo
 * `dueIso`, cualquier rango escondería las órdenes sin agendar (y una entregada
 * que saltó etapas sin fecha nunca sumaría como ingreso).
 */
export function orderDayIso(o: Pick<Order, "dueIso" | "createdIso">): string {
  return o.dueIso || o.createdIso;
}

/** Valor del pedido SIN envío: lo que se muestra al marcar "en camino", donde
 *  el operador escribe el envío real encima (sumar al `total` contaría el
 *  envío estimado dos veces). */
export function orderValueWithoutShipping(o: Pick<Order, "total" | "shipping">): number {
  return o.total - (o.shipping ?? 0);
}

/** ¿La orden entra en los números de la pantalla? Un pedido de prueba no:
 *  ni contadores, ni KPIs, ni totales de columna, ni ingresos. */
export function countsInStats(o: Pick<Order, "isTest">): boolean {
  return !o.isTest;
}

/** Ingreso = plata que YA entró: columna "Entregada" y pago "Pagado" (y no
 *  es un pedido de prueba). */
export function isCollectedRevenue(
  o: Pick<Order, "status" | "payStatus" | "isTest">,
): boolean {
  return o.status === "delivered" && o.payStatus === "paid" && countsInStats(o);
}

/** Una fila de un desglose del reporte de ventas: la clave (día, etapa, canal,
 *  ciudad) con cuántos pedidos y cuánto suman. */
export interface SalesBreakdown {
  key: string;
  orders: number;
  sales: number;
}

export interface SalesTotals {
  orders: number;
  sales: number;
}

/** Reporte de ventas de un rango (tarjeta del ciclo #18). */
export interface SalesReport {
  orders: number;
  sales: number;
  averageTicket: number;
  shipping: number;
  collected: number;
  toCollect: number;
  /** Pagado (prepago) pero todavía no entregado: está dentro de `toCollect`
   *  porque «Cobrado» es la cuenta del KPI «Ingresos» (entregado y pagado). */
  paidNotDelivered: number;
  refunded: SalesTotals;
  /** Envío estimado al vender (tarifa mínima) y el real que fijó el operador
   *  al despachar; `storeShippingCost` = lo que paga la tienda en los
   *  pedidos con envío gratis. */
  shippingEstimated: number;
  shippingReal: number;
  storeShippingCost: number;
  byPayType: Record<PayType, SalesTotals>;
  byStatus: SalesBreakdown[];
  byDay: SalesBreakdown[];
  byChannel: SalesBreakdown[];
  byCity: SalesBreakdown[];
  cancelled: SalesTotals;
}

function breakdown(orders: Order[], keyOf: (o: Order) => string): SalesBreakdown[] {
  const rows = new Map<string, SalesBreakdown>();
  for (const o of orders) {
    const key = keyOf(o) || "—";
    const row = rows.get(key) ?? { key, orders: 0, sales: 0 };
    row.orders += 1;
    row.sales += o.total;
    rows.set(key, row);
  }
  return [...rows.values()];
}

/** Orden de las etapas en el desglose: el mismo de las columnas del tablero. */
const STATUS_ORDER: OrderStatus[] = ["new", "preparing", "ready", "shipping", "delivered", "delayed", "cancelled"];

/**
 * Reporte de ventas de un rango (tarjeta del ciclo #18). Con las mismas reglas
 * que la vista Orders, así que cuadra con ella por construcción:
 *   - cada pedido cae en su día (`orderDayIso`: entrega, o creación);
 *   - los de prueba no cuentan (`countsInStats`);
 *   - los cancelados no son ventas: van en `cancelled`;
 *   - `collected` es la cuenta del KPI «Ingresos» (`isCollectedRevenue`).
 * `sales` es el total vigente del pedido (con envío); `shipping` es la parte
 * de envío incluida.
 */
export function salesReport(orders: Order[], range: DateRange): SalesReport {
  // El mismo corte de días que el calendario de la vista.
  const inPeriod = orders.filter((o) => countsInStats(o) && isInDateRange(orderDayIso(o), range));
  const sold = inPeriod.filter((o) => o.status !== "cancelled");
  const cancelled = inPeriod.filter((o) => o.status === "cancelled");
  const sum = (list: Order[], value: (o: Order) => number) => list.reduce((a, o) => a + value(o), 0);
  const totals = (list: Order[]): SalesTotals => ({ orders: list.length, sales: sum(list, (o) => o.total) });

  const sales = sum(sold, (o) => o.total);
  const collected = sum(sold.filter(isCollectedRevenue), (o) => o.total);
  // Un estado que no esté en la lista va al final (no primero, con -1).
  const rank = (key: string) => {
    const i = STATUS_ORDER.indexOf(key as OrderStatus);
    return i === -1 ? STATUS_ORDER.length : i;
  };
  const byStatus = breakdown(sold, (o) => o.status).sort((a, b) => rank(a.key) - rank(b.key));
  const bySales = (a: SalesBreakdown, b: SalesBreakdown) => b.sales - a.sales || a.key.localeCompare(b.key);

  return {
    orders: sold.length,
    sales,
    averageTicket: sold.length ? Math.round(sales / sold.length) : 0,
    shipping: sum(sold, (o) => o.shipping ?? 0),
    collected,
    toCollect: sales - collected,
    paidNotDelivered: sum(
      sold.filter((o) => o.payStatus === "paid" && o.status !== "delivered"),
      (o) => o.total,
    ),
    refunded: totals(sold.filter((o) => o.payStatus === "refund")),
    shippingEstimated: sum(sold.filter((o) => !o.shippingConfirmed), (o) => o.shipping ?? 0),
    shippingReal: sum(sold.filter((o) => o.shippingConfirmed), (o) => o.shipping ?? 0),
    storeShippingCost: sum(sold.filter((o) => o.freeShipping), (o) => o.storeShippingCost ?? 0),
    byPayType: {
      cod: totals(sold.filter((o) => o.payType === "cod")),
      confirmed: totals(sold.filter((o) => o.payType === "confirmed")),
    },
    byStatus,
    byDay: breakdown(sold, orderDayIso).sort((a, b) => a.key.localeCompare(b.key)),
    byChannel: breakdown(sold, (o) => o.channel).sort(bySales),
    byCity: breakdown(sold, (o) => o.city).sort(bySales),
    cancelled: totals(cancelled),
  };
}

/* ── Reporte por producto (tarjeta del ciclo #49) ─────────────────────── */

/** Aviso cuando no hay de dónde leer costo ni línea de producto. */
export const NO_COST_SOURCE_NOTE = "Sin fuente de costo ni de línea: estas columnas quedan vacías";

/** Costo y línea de un producto, de la fuente que indique el operador. */
export interface ProductCost {
  unitCost: number | null;
  line: string | null;
  fullUnitCost: number | null;
}

/** La ÚNICA entrada para costo y línea: hoy no hay fuente (`NO_PRODUCT_COSTS`). */
export type ProductCostSource = (item: OrderItemDetail) => ProductCost | null;

export const NO_PRODUCT_COSTS: ProductCostSource = () => null;

/** Una fila del reporte por producto: un producto vendido en un pedido. */
export interface ProductSaleRow {
  product: string;
  variant: string;
  quantity: number;
  unitCost: number | null;
  unitPrice: number;
  totalCost: number | null;
  saleWithoutShipping: number;
  profit: number | null;
  /** Porcentaje sobre la venta, con un decimal. */
  margin: number | null;
  customer: string;
  orderId: string;
  line: string | null;
  fullUnitCost: number | null;
  paymentChannel: string;
  listPrice: number | null;
  /** Descuento de la línea (precio de lista − precio cobrado) × cantidad. */
  discount: number;
  discountReason: string;
  storePaysShipping: boolean;
  /** Lo que pagó la tienda por el envío gratis: solo en la primera fila del
   *  pedido, para que la columna sume una vez por pedido. */
  storeShippingCost: number | null;
}

export interface ProductLineTotals {
  line: string;
  quantity: number;
  sales: number;
  profit: number;
  margin: number | null;
}

export interface ProductSalesReport {
  rows: ProductSaleRow[];
  /** Suma de «Venta total sin envío». */
  sales: number;
  /** Aviso de las celdas de costo/línea vacías; null si no falta ninguna. */
  costNote: string | null;
  byLine: ProductLineTotals[];
  /** Pedidos cuyas líneas no suman su valor sin envío (valor − líneas). */
  mismatches: { orderId: string; difference: number }[];
  /** Pedidos del rango sin sus líneas cargadas. */
  ordersWithoutLines: string[];
}

const PAY_TYPE_LABEL: Record<PayType, string> = { cod: "Contra entrega", confirmed: "Pago confirmado" };

/** Margen en porcentaje con un decimal; sin venta no hay margen. */
function marginPct(profit: number, sales: number): number | null {
  return sales ? Math.round((profit / sales) * 1000) / 10 : null;
}

function variantOf(it: OrderItemDetail): string {
  return it.variant_label ?? it.selected_variant_title ?? [it.color, it.aroma].filter(Boolean).join(" · ");
}

function discountReasonOf(it: OrderItemDetail): string {
  if (it.coupon_code) return `Cupón ${it.coupon_code}`;
  if (it.price_adjusted_reason) return `Precio ajustado: ${it.price_adjusted_reason}`;
  return "";
}

/**
 * Reporte de ventas por producto (tarjeta del ciclo #49): una fila por
 * producto vendido, con las MISMAS reglas que `salesReport` (mismo día por
 * pedido, sin pruebas, cancelados fuera), así que «Venta total sin envío»
 * suma lo mismo que las ventas del reporte por pedido menos el envío
 * incluido. Si las líneas de un pedido no suman su valor sin envío, va en
 * `mismatches`; si un pedido no trae sus líneas, en `ordersWithoutLines`.
 *
 * Costo, ganancia, margen y línea salen SOLO de `costs`: sin dato, la celda
 * queda en null (vacía y avisada en `costNote`), nunca un cero.
 */
export function salesReportByProduct(
  orders: Order[],
  lines: OrderLines[],
  range: DateRange,
  costs: ProductCostSource = NO_PRODUCT_COSTS,
): ProductSalesReport {
  const linesById = new Map(lines.map((l) => [l.display_id, l]));
  const sold = orders.filter(
    (o) => countsInStats(o) && isInDateRange(orderDayIso(o), range) && o.status !== "cancelled",
  );
  const rows: ProductSaleRow[] = [];
  const mismatches: ProductSalesReport["mismatches"] = [];
  const ordersWithoutLines: string[] = [];

  for (const o of sold) {
    const ol = linesById.get(o.id);
    if (!ol) {
      ordersWithoutLines.push(o.id);
      continue;
    }
    const difference = orderValueWithoutShipping(o) - ol.items.reduce((a, it) => a + it.total_cop, 0);
    if (difference !== 0) mismatches.push({ orderId: o.id, difference });
    const storePaysShipping = !!o.freeShipping;
    ol.items.forEach((it, i) => {
      const cost = costs(it);
      const unitCost = cost?.unitCost ?? null;
      const totalCost = unitCost === null ? null : unitCost * it.quantity;
      const profit = totalCost === null ? null : it.total_cop - totalCost;
      const listPrice = it.list_unit_price_cop;
      rows.push({
        product: it.title,
        variant: variantOf(it),
        quantity: it.quantity,
        unitCost,
        unitPrice: it.unit_price_cop,
        totalCost,
        saleWithoutShipping: it.total_cop,
        profit,
        margin: profit === null ? null : marginPct(profit, it.total_cop),
        customer: o.customer,
        orderId: o.id,
        line: cost?.line ?? null,
        fullUnitCost: cost?.fullUnitCost ?? null,
        paymentChannel: ol.payment_method_label ?? PAY_TYPE_LABEL[o.payType],
        listPrice,
        discount: listPrice !== null && listPrice > it.unit_price_cop ? (listPrice - it.unit_price_cop) * it.quantity : 0,
        discountReason: discountReasonOf(it),
        storePaysShipping,
        storeShippingCost: storePaysShipping && i === 0 ? (o.storeShippingCost ?? null) : null,
      });
    });
  }

  const missing = rows.filter((r) => r.unitCost === null || r.line === null).length;
  let costNote: string | null = null;
  if (rows.length > 0 && missing === rows.length) costNote = NO_COST_SOURCE_NOTE;
  else if (missing > 0) costNote = `${missing} de ${rows.length} filas sin costo o sin línea: esas celdas quedan vacías`;

  // Totales por línea: solo filas con línea Y costo (ganancia sin costo sería inventada).
  const byLineMap = new Map<string, ProductLineTotals>();
  for (const r of rows) {
    if (r.line === null || r.profit === null) continue;
    const t = byLineMap.get(r.line) ?? { line: r.line, quantity: 0, sales: 0, profit: 0, margin: null };
    t.quantity += r.quantity;
    t.sales += r.saleWithoutShipping;
    t.profit += r.profit;
    byLineMap.set(r.line, t);
  }
  const byLine = [...byLineMap.values()]
    .map((t) => ({ ...t, margin: marginPct(t.profit, t.sales) }))
    .sort((a, b) => b.sales - a.sales || a.line.localeCompare(b.line));

  return {
    rows,
    sales: rows.reduce((a, r) => a + r.saleWithoutShipping, 0),
    costNote,
    byLine,
    mismatches,
    ordersWithoutLines,
  };
}

export interface OrderStatusMeta {
  label: string;
  color: string;
  bg: string;
}

export const ORDER_STATUS_META: Record<OrderStatus, OrderStatusMeta> = {
  new:       { label: "Nueva",          color: "var(--color-info)", bg: "var(--color-info-soft)" },
  preparing: { label: "En preparación", color: "var(--color-warn)", bg: "var(--color-warn-soft)" },
  ready:     { label: "Lista",          color: "var(--color-violet)", bg: "var(--color-violet-soft)" },
  shipping:  { label: "En camino",      color: "var(--color-cyan)", bg: "var(--color-cyan-soft)" },
  delivered: { label: "Entregada",      color: "var(--color-ok)", bg: "var(--color-ok-soft)" },
  delayed:   { label: "Retrasada",      color: "var(--color-danger)", bg: "rgba(255,114,105,0.2)"  },
  cancelled: { label: "Cancelada",      color: "var(--color-neutral)", bg: "var(--color-neutral-soft)" },
};

export const PAY_STATUS_META: Record<PayStatus, { label: string; color: string }> = {
  paid:    { label: "Pagado",      color: "var(--color-ok)" },
  partial: { label: "Parcial",     color: "var(--color-warn)" },
  pending: { label: "Pendiente",   color: "var(--color-danger)" },
  refund:  { label: "Reembolsado", color: "var(--color-neutral)" },
};

// Re-export the backend types for components that need the richer detail.
export type { OrderDetail, OrderItemDetail, OrderAddress, OrderLines, OrderSummary, OrderTimelineEvent };
