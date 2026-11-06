import type { DateRange } from "@/shared/lib";
import type { Order, SalesReport } from "@plugins/orders/frontend/entities/order";

export function salesReportCsv(_report: SalesReport, _orders: Order[], _range: DateRange): string {
  return "";
}
