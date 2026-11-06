/**
 * El CSV del reporte de ventas (tarjeta del ciclo #18): el resumen con las
 * mismas cifras que se ven en pantalla y, debajo, una fila por pedido del
 * rango (sin pedidos de prueba), para que el equipo pueda revisar la cuenta.
 * Montos en pesos enteros, sin formato: Excel los suma.
 */
import { isInDateRange, toCsv, type CsvCell, type DateRange } from "@/shared/lib";
import {
  ORDER_STATUS_META,
  PAY_STATUS_META,
  countsInStats,
  orderDayIso,
  type Order,
  type SalesReport,
} from "@plugins/orders/frontend/entities/order";

const PAY_TYPE_LABEL = { cod: "Contra entrega", confirmed: "Pago confirmado" } as const;

export function salesReportCsv(report: SalesReport, orders: Order[], range: DateRange): string {
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
  return toCsv(rows);
}
