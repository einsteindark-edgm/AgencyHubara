/**
 * Sección «Por producto» del reporte de ventas (tarjeta del ciclo #49): una
 * fila por producto vendido con las columnas que pidió el equipo, el aviso de
 * las columnas sin fuente (costo, ganancia, margen, línea) y los pedidos que
 * no cuadran o no trajeron sus líneas. Las cifras salen de
 * `salesReportByProduct`, las mismas que baja el CSV.
 */
import { fmtMoney } from "@/shared/lib";
import type { OrderLinesResponse, ProductSalesReport } from "@plugins/orders/frontend/entities/order";
import { PRODUCT_REPORT_COLUMNS } from "../model/salesReportCsv";

const money = (v: number | null) => (v === null ? "" : fmtMoney(v));

interface Props {
  lines: { data?: OrderLinesResponse; isLoading: boolean };
  report: ProductSalesReport | null;
}

export function ProductSales({ lines, report }: Props) {
  let body;
  if (lines.isLoading || (!lines.data && !report)) {
    body = <p className="m-0 text-xs text-fg-muted">Cargando el detalle por producto…</p>;
  } else if (!lines.data?.catalog_available || !report) {
    body = (
      <p className="m-0 text-xs text-fg-muted">
        No se pudo traer el detalle por producto{lines.data?.error_detail ? `: ${lines.data.error_detail}` : ""}.
      </p>
    );
  } else {
    body = (
      <>
        {report.costNote ? (
          <p
            role="note"
            aria-label="Columnas sin costo ni línea"
            className="m-0 rounded-lg border border-line px-3 py-2 text-xs text-fg-muted"
          >
            {report.costNote}
          </p>
        ) : null}
        {report.mismatches.length > 0 ? (
          <p
            role="note"
            aria-label="Pedidos que no cuadran"
            className="m-0 rounded-lg border border-line px-3 py-2 text-xs text-fg-muted"
          >
            Las líneas de estos pedidos no suman su valor sin envío:{" "}
            {report.mismatches.map((m) => `${m.orderId} (${fmtMoney(m.difference)})`).join(", ")}.
          </p>
        ) : null}
        {report.ordersWithoutLines.length > 0 ? (
          <p className="m-0 text-xs text-fg-muted">
            Sin detalle de productos: {report.ordersWithoutLines.join(", ")}.
          </p>
        ) : null}
        {report.rows.length === 0 ? (
          <p className="m-0 text-xs text-fg-muted">Sin productos vendidos.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-max min-w-full text-[12px]">
              <thead>
                <tr className="text-[10px] uppercase tracking-wider text-fg-faint">
                  {PRODUCT_REPORT_COLUMNS.map((c) => (
                    <th key={c} scope="col" className="py-1 pr-3 text-left font-semibold whitespace-nowrap">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {report.rows.map((r, i) => (
                  <tr key={`${r.orderId}-${i}`} className="border-b border-line last:border-b-0">
                    <td className="py-1 pr-3 text-fg-soft">{r.product}</td>
                    <td className="py-1 pr-3">{r.variant}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{r.quantity}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{money(r.unitCost)}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{fmtMoney(r.unitPrice)}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{money(r.totalCost)}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{fmtMoney(r.saleWithoutShipping)}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{money(r.profit)}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{r.margin === null ? "" : `${r.margin} %`}</td>
                    <td className="py-1 pr-3">{r.customer}</td>
                    <td className="py-1 pr-3">{r.orderId}</td>
                    <td className="py-1 pr-3">{r.line ?? ""}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{money(r.fullUnitCost)}</td>
                    <td className="py-1 pr-3">{r.paymentChannel}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{money(r.listPrice)}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{r.discount ? fmtMoney(r.discount) : ""}</td>
                    <td className="py-1 pr-3">{r.discountReason}</td>
                    <td className="py-1 pr-3">{r.storePaysShipping ? "Sí" : "No"}</td>
                    <td className="py-1 text-right tabular-nums">{money(r.storeShippingCost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </>
    );
  }

  return (
    <section aria-label="Por producto" className="grid min-w-0 gap-2">
      <h3 className="m-0 text-[11px] font-semibold uppercase tracking-wider text-fg-faint">Por producto</h3>
      {body}
    </section>
  );
}
