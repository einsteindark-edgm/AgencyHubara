/**
 * Sección «Por producto» del reporte de ventas (tarjeta del ciclo #49): una
 * fila por producto vendido con las columnas que pidió el equipo, el aviso de
 * las columnas sin fuente (costo, ganancia, margen, línea) y los pedidos que
 * no cuadran o no trajeron sus líneas. Las cifras salen de
 * `salesReportByProduct`, las mismas que baja el CSV.
 */
import type { ReactNode } from "react";
import { fmtMoney } from "@/shared/lib";
import type { OrderLinesResponse, ProductSaleRow, ProductSalesReport } from "@plugins/orders/frontend/entities/order";
import { PRODUCT_REPORT_COLUMNS } from "../model/salesReportCsv";

const money = (v: number | null) => (v === null ? "" : fmtMoney(v));

interface Column {
  label: (typeof PRODUCT_REPORT_COLUMNS)[number];
  cell: (r: ProductSaleRow) => ReactNode;
  numeric?: boolean;
  /** La fila tiene dato en esta columna. Sin dato en ninguna fila del rango,
   *  la columna no se muestra (2026-11-19: sin fuente de costo, seis columnas
   *  vacías empujaban el pedido fuera de la vista). El CSV las trae todas. */
  has?: (r: ProductSaleRow) => boolean;
}

/** En el orden de `PRODUCT_REPORT_COLUMNS` (el que pidió el equipo). */
const COLUMNS: Column[] = [
  { label: "Producto", cell: (r) => r.product },
  { label: "Producto en el catálogo (variante)", cell: (r) => r.variant, has: (r) => r.variant !== "" },
  { label: "Cantidad", cell: (r) => r.quantity, numeric: true },
  { label: "Costo unitario", cell: (r) => money(r.unitCost), numeric: true, has: (r) => r.unitCost !== null },
  { label: "Precio unitario", cell: (r) => fmtMoney(r.unitPrice), numeric: true },
  { label: "Costo total", cell: (r) => money(r.totalCost), numeric: true, has: (r) => r.totalCost !== null },
  { label: "Venta total sin envío", cell: (r) => fmtMoney(r.saleWithoutShipping), numeric: true },
  { label: "Ganancia", cell: (r) => money(r.profit), numeric: true, has: (r) => r.profit !== null },
  { label: "Margen", cell: (r) => (r.margin === null ? "" : `${r.margin} %`), numeric: true, has: (r) => r.margin !== null },
  { label: "Cliente", cell: (r) => r.customer },
  { label: "Número de pedido", cell: (r) => r.orderId },
  { label: "Línea de producto", cell: (r) => r.line ?? "", has: (r) => r.line !== null },
  {
    label: "Costo completo unitario",
    cell: (r) => money(r.fullUnitCost),
    numeric: true,
    has: (r) => r.fullUnitCost !== null,
  },
  { label: "Canal de pago", cell: (r) => r.paymentChannel },
  { label: "Precio de lista", cell: (r) => money(r.listPrice), numeric: true, has: (r) => r.listPrice !== null },
  {
    label: "Descuento",
    cell: (r) => (r.discount ? fmtMoney(r.discount) : ""),
    numeric: true,
    has: (r) => r.discount > 0,
  },
  { label: "Motivo del descuento", cell: (r) => r.discountReason, has: (r) => r.discountReason !== "" },
  { label: "Envío asumido por la tienda", cell: (r) => (r.storePaysShipping ? "Sí" : "No"), has: (r) => r.storePaysShipping },
  {
    label: "Costo del envío asumido",
    cell: (r) => money(r.storeShippingCost),
    numeric: true,
    has: (r) => r.storeShippingCost !== null,
  },
];

interface Props {
  lines: { data?: OrderLinesResponse; isLoading: boolean; isError?: boolean };
  report: ProductSalesReport | null;
}

export function ProductSales({ lines, report }: Props) {
  let body;
  if (!lines.isError && (lines.isLoading || (!lines.data && !report))) {
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
          <ProductTable rows={report.rows} />
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

/** Una fila por producto, agrupadas por pedido (vienen en el orden de los
 *  pedidos); solo las columnas con algún dato en el rango. */
function ProductTable({ rows }: { rows: ProductSaleRow[] }) {
  const columns = COLUMNS.filter((c) => !c.has || rows.some(c.has));
  return (
    <div className="overflow-x-auto">
      <table className="w-max min-w-full text-[12px]">
        <thead>
          <tr className="text-[10px] uppercase tracking-wider text-fg-faint">
            {columns.map((c) => (
              <th key={c.label} scope="col" className="py-1 pr-3 text-left font-semibold whitespace-nowrap">
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.orderId}-${i}`} className="border-b border-line last:border-b-0">
              {columns.map((c) => (
                <td key={c.label} className={`py-1 pr-3 ${c.numeric ? "text-right tabular-nums" : ""}`}>
                  {c.cell(r)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
