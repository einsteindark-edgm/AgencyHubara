/**
 * El CSV del reporte de ventas (tarjeta del ciclo #18): el resumen con las
 * mismas cifras que se ven en pantalla y, debajo, una fila por pedido del
 * rango (sin pedidos de prueba), para que el equipo pueda revisar la cuenta.
 * Montos en pesos enteros, sin formato: Excel los suma.
 *
 * Con el reporte por producto (tarjeta del ciclo #49), debajo va una tabla
 * con una fila por producto vendido, las columnas en el orden que pidió el
 * equipo y el aviso de las celdas sin fuente (vacías, nunca un cero).
 */
import { isInDateRange, toCsv, type CsvCell, type DateRange } from "@/shared/lib";
import {
  ORDER_STATUS_META,
  PAY_STATUS_META,
  countsInStats,
  orderDayIso,
  type Order,
  type ProductSalesReport,
  type SalesReport,
} from "@plugins/orders/frontend/entities/order";

const PAY_TYPE_LABEL = { cod: "Contra entrega", confirmed: "Pago confirmado" } as const;

/** Columnas del reporte por producto, en el orden pedido (las 14 del equipo
 *  y después las marcas de descuento y envío). La pantalla usa las mismas. */
export const PRODUCT_REPORT_COLUMNS = [
  "Producto",
  "Producto en el catálogo (variante)",
  "Cantidad",
  "Costo unitario",
  "Precio unitario",
  "Costo total",
  "Venta total sin envío",
  "Ganancia",
  "Margen",
  "Cliente",
  "Número de pedido",
  "Línea de producto",
  "Costo completo unitario",
  "Canal de pago",
  "Precio de lista",
  "Descuento",
  "Motivo del descuento",
  "Envío asumido por la tienda",
  "Costo del envío asumido",
] as const;

function productRows(byProduct: ProductSalesReport): CsvCell[][] {
  const empty = (v: number | string | null): CsvCell => v ?? "";
  const rows: CsvCell[][] = [[], ["Por producto"], ["Venta total sin envío", byProduct.sales]];
  if (byProduct.costNote) rows.push([byProduct.costNote]);
  for (const m of byProduct.mismatches) {
    rows.push([`Las líneas del pedido ${m.orderId} no cuadran con su valor sin envío`, m.difference]);
  }
  for (const id of byProduct.ordersWithoutLines) rows.push([`Pedido ${id} sin detalle de productos`]);
  rows.push([...PRODUCT_REPORT_COLUMNS]);
  for (const r of byProduct.rows) {
    rows.push([
      r.product,
      r.variant,
      r.quantity,
      empty(r.unitCost),
      r.unitPrice,
      empty(r.totalCost),
      r.saleWithoutShipping,
      empty(r.profit),
      empty(r.margin),
      r.customer,
      r.orderId,
      empty(r.line),
      empty(r.fullUnitCost),
      r.paymentChannel,
      empty(r.listPrice),
      r.discount,
      r.discountReason,
      r.storePaysShipping ? "Sí" : "No",
      empty(r.storeShippingCost),
    ]);
  }
  if (byProduct.byLine.length > 0) {
    rows.push([], ["Línea de producto", "Cantidad", "Ventas", "Ganancia", "Margen"]);
    for (const t of byProduct.byLine) rows.push([t.line, t.quantity, t.sales, t.profit, empty(t.margin)]);
  }
  return rows;
}

export function salesReportCsv(
  report: SalesReport,
  orders: Order[],
  range: DateRange,
  byProduct?: ProductSalesReport,
): string {
  const rows: CsvCell[][] = [
    ["Reporte de ventas"],
    ["Desde", range.from ?? "Todas las fechas"],
    ["Hasta", range.to ?? range.from ?? "Todas las fechas"],
    [],
    ["Pedidos", report.orders],
    ["Ventas", report.sales],
    ["Ticket promedio", report.averageTicket],
    ["Envío incluido", report.shipping],
    ["Cobrado", report.collected],
    ["Por cobrar", report.toCollect],
    ["Pagado sin entregar", report.paidNotDelivered],
    ["Envío estimado", report.shippingEstimated],
    ["Envío real", report.shippingReal],
    ["Envío que paga la tienda", report.storeShippingCost],
    ["Reembolsos", report.refunded.orders, report.refunded.sales],
    ["Contra entrega", report.byPayType.cod.orders, report.byPayType.cod.sales],
    ["Pago confirmado", report.byPayType.confirmed.orders, report.byPayType.confirmed.sales],
    ["Cancelados", report.cancelled.orders, report.cancelled.sales],
    [],
    ["Pedido", "Día", "Cliente", "Ciudad", "Canal", "Etapa", "Pago", "Modalidad", "Total", "Envío"],
  ];
  const inPeriod = orders.filter((o) => countsInStats(o) && isInDateRange(orderDayIso(o), range));
  for (const o of inPeriod) {
    rows.push([
      o.id,
      orderDayIso(o),
      o.customer,
      o.city,
      o.channel,
      ORDER_STATUS_META[o.status].label,
      PAY_STATUS_META[o.payStatus].label,
      PAY_TYPE_LABEL[o.payType],
      o.total,
      o.shipping ?? 0,
    ]);
  }
  if (byProduct) rows.push(...productRows(byProduct));
  return toCsv(rows);
}
