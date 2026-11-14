/**
 * Reporte de ventas por fechas (tarjeta del ciclo #18): lo abren los dos
 * «Exportar» de Órdenes (cabecera y barra lateral).
 *
 * Arranca con el rango activo de la vista y se puede cambiar acá sin tocar la
 * vista (el rango del diálogo es UI local). Las cifras salen de `salesReport`
 * sobre los mismos pedidos que pinta Orders, sin pedidos de prueba, así que
 * cuadran con ella. «Descargar CSV» baja el resumen y una fila por pedido.
 *
 * Límite: Orders carga los pedidos más recientes (el listado tiene tope). Si se
 * cargó el tope y el rango no tiene inicio, o empieza antes del pedido más
 * viejo cargado, el reporte avisa que puede faltar historia.
 *
 * Alcance: todas las modalidades, sin la búsqueda ni la vista del tablero (el
 * reporte es del periodo, como el KPI «Ingresos»). El diálogo lo dice.
 *
 * «Por producto» (tarjeta del ciclo #49): las líneas de los pedidos se piden
 * solo al abrir el diálogo (`useOrderLines` vive en `ExportBody`, que no se
 * monta cerrado) y salen también en el CSV.
 */
import { useId, useState } from "react";
import {
  NO_DATE_RANGE,
  describeDateRange,
  downloadText,
  fmtMoney,
  formatIsoDateEs,
  type DateRange,
} from "@/shared/lib";
import { DateRangeFilter, MacButton, Modal } from "@/shared/ui";
import {
  ORDER_STATUS_META,
  orderDayIso,
  salesReport,
  salesReportByProduct,
  useOrderLines,
  type Order,
  type OrderStatus,
  type SalesBreakdown,
} from "@plugins/orders/frontend/entities/order";
import { salesReportCsv } from "../model/salesReportCsv";
import { ProductSales } from "./ProductSales";

interface Props {
  open: boolean;
  onClose: () => void;
  /** Todos los pedidos que cargó Orders (el reporte saca los de prueba). */
  orders: Order[];
  /** Tope del listado de Orders (`limit` de la respuesta); con ese número de
   *  pedidos cargados puede faltar historia. */
  listLimit?: number;
  /** Rango activo de la vista al abrir. */
  initialRange: DateRange;
  activeDays: Set<string>;
  /** Hoy en Bogotá (lo lee el dueño en render). */
  today: string;
}

function Metric({ label, value, money = true }: { label: string; value: number; money?: boolean }) {
  return (
    <div data-metric data-value={value} className="rounded-lg border border-line bg-white/[0.03] px-3 py-2">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-fg-faint">{label}</div>
      <div className="mt-0.5 text-[15px] font-semibold tabular-nums">{money ? fmtMoney(value) : value}</div>
    </div>
  );
}

function Breakdown({
  title,
  rows,
  label = (k) => k,
}: {
  title: string;
  rows: SalesBreakdown[];
  label?: (key: string) => string;
}) {
  return (
    <section aria-label={title} className="min-w-0">
      <h3 className="m-0 mb-1 text-[11px] font-semibold uppercase tracking-wider text-fg-faint">{title}</h3>
      {rows.length === 0 ? (
        <p className="m-0 text-xs text-fg-muted">Sin pedidos.</p>
      ) : (
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-[10px] uppercase tracking-wider text-fg-faint">
              <th scope="col" className="py-1 pr-2 text-left font-semibold">
                <span className="sr-only">Detalle</span>
              </th>
              <th scope="col" aria-label="Pedidos" className="py-1 pr-2 text-right font-semibold">
                Cant.
              </th>
              <th scope="col" className="py-1 text-right font-semibold">Ventas</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-b border-line last:border-b-0">
                <td className="py-1 pr-2 text-fg-soft">{label(r.key)}</td>
                <td className="py-1 pr-2 text-right tabular-nums text-fg-muted">{r.orders}</td>
                <td className="py-1 text-right tabular-nums">{fmtMoney(r.sales)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function OrdersExportDialog({ open, ...props }: Props) {
  if (!open) return null;
  return <ExportBody {...props} />;
}

function ExportBody({ onClose, orders, listLimit, initialRange, activeDays, today }: Omit<Props, "open">) {
  const titleId = useId();
  const [range, setRange] = useState<DateRange>(initialRange);
  const report = salesReport(orders, range);
  const lines = useOrderLines();
  const byProduct =
    lines.data?.catalog_available ? salesReportByProduct(orders, lines.data.orders, range) : undefined;
  const rangeLabel = range.from ? describeDateRange(range, today) : "Todas las fechas";

  const loadedDays = orders.map(orderDayIso).filter(Boolean).sort();
  const oldestLoaded = loadedDays[0];
  const atLimit = listLimit !== undefined && orders.length >= listLimit;
  const mayMissHistory = atLimit && (!range.from || (!!oldestLoaded && range.from < oldestLoaded));

  const download = () => {
    const name = range.from ? `ventas_${range.from}_${range.to ?? range.from}.csv` : "ventas_todas.csv";
    downloadText(name, salesReportCsv(report, orders, range, byProduct));
  };

  return (
    <Modal open labelledBy={titleId} onClose={onClose} className="min-[760px]:max-w-[760px]!">
      <div className="flex items-start gap-3 border-b border-line bg-titlebar px-4 py-3">
        <div className="min-w-0">
          <h2 id={titleId} className="m-0 text-[15px] font-semibold">
            Reporte de ventas
          </h2>
          <p className="m-0 mt-0.5 text-xs text-fg-muted">
            {rangeLabel} · todas las modalidades, sin la búsqueda ni la vista del tablero · sin pedidos de prueba
          </p>
        </div>
        <button
          type="button"
          aria-label="Cerrar"
          onClick={onClose}
          className="ml-auto h-8 w-8 flex-none rounded-lg border border-line bg-white/[0.06] text-base text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          ✕
        </button>
      </div>

      <div className="grid gap-4 overflow-y-auto px-4 py-3 text-[13px]">
        <DateRangeFilter
          value={range}
          onChange={setRange}
          onClear={() => setRange(NO_DATE_RANGE)}
          activeDays={activeDays}
          today={today}
          label={rangeLabel}
        />

        {mayMissHistory ? (
          <p
            role="note"
            aria-label="Puede faltar historia"
            className="m-0 rounded-lg border border-line px-3 py-2 text-xs text-fg-muted"
          >
            Orders carga los {listLimit} pedidos más recientes
            {oldestLoaded ? `: el más viejo cargado es del ${formatIsoDateEs(oldestLoaded)}` : ""}. Antes de esa
            fecha puede faltar historia.
          </p>
        ) : null}

        <div className="grid grid-cols-2 gap-2 min-[560px]:grid-cols-3">
          <Metric label="Pedidos" value={report.orders} money={false} />
          <Metric label="Ventas" value={report.sales} />
          <Metric label="Ticket promedio" value={report.averageTicket} />
          <Metric label="Cobrado" value={report.collected} />
          <Metric label="Por cobrar" value={report.toCollect} />
          <Metric label="Pagado sin entregar" value={report.paidNotDelivered} />
          <Metric label="Envío incluido" value={report.shipping} />
          <Metric label="Envío estimado" value={report.shippingEstimated} />
          <Metric label="Envío real" value={report.shippingReal} />
          <Metric label="Envío que paga la tienda" value={report.storeShippingCost} />
          <Metric label="Contra entrega" value={report.byPayType.cod.sales} />
          <Metric label="Pago confirmado" value={report.byPayType.confirmed.sales} />
          <Metric label="Reembolsos" value={report.refunded.sales} />
          <Metric label="Cancelados" value={report.cancelled.sales} />
        </div>
        <p className="m-0 text-xs text-fg-muted">
          «Cobrado» es lo entregado y pagado, la misma cuenta del KPI «Ingresos». «Por cobrar» es el resto e
          incluye lo pagado que falta entregar.
        </p>

        <div className="grid gap-4 min-[560px]:grid-cols-2">
          <Breakdown
            title="Por etapa"
            rows={report.byStatus}
            label={(k) => ORDER_STATUS_META[k as OrderStatus]?.label ?? k}
          />
          <Breakdown title="Por día" rows={report.byDay} label={formatIsoDateEs} />
          <Breakdown title="Por canal" rows={report.byChannel} />
          <Breakdown title="Por ciudad" rows={report.byCity} />
        </div>

        <ProductSales lines={lines} report={byProduct ?? null} />
      </div>

      <div className="flex justify-end gap-2 border-t border-line px-4 py-3">
        {/* El foco entra al diálogo (el Modal no lo mueve solo). */}
        {/* eslint-disable-next-line jsx-a11y/no-autofocus */}
        <MacButton sm onClick={onClose} autoFocus>
          Cerrar
        </MacButton>
        <MacButton sm primary onClick={download} disabled={report.orders === 0 && report.cancelled.orders === 0}>
          Descargar CSV
        </MacButton>
      </div>
    </Modal>
  );
}
