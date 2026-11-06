/**
 * Reporte de ventas por fechas (tarjeta del ciclo #18): lo abren los dos
 * «Exportar» de Órdenes (cabecera y barra lateral).
 *
 * Arranca con el rango activo de la vista y se puede cambiar acá sin tocar la
 * vista (el rango del diálogo es UI local). Las cifras salen de `salesReport`
 * sobre los mismos pedidos que pinta Orders, sin pedidos de prueba, así que
 * cuadran con ella. «Descargar CSV» baja el resumen y una fila por pedido.
 *
 * Límite: Orders carga los pedidos más recientes (el listado tiene tope). Si el
 * rango empieza antes del pedido más viejo cargado, el reporte lo avisa: puede
 * faltar historia.
 */
import { useId, useState } from "react";
import {
  NO_DATE_RANGE,
  describeDateRange,
  downloadText,
  type DateRange,
} from "@/shared/lib";
import { fmtMoney } from "@/shared/lib/format";
import { DateRangeFilter, MacButton, Modal } from "@/shared/ui";
import {
  ORDER_STATUS_META,
  orderDayIso,
  salesReport,
  type Order,
  type OrderStatus,
  type SalesBreakdown,
} from "@plugins/orders/frontend/entities/order";
import { salesReportCsv } from "../model/salesReportCsv";

interface Props {
  open: boolean;
  onClose: () => void;
  /** Todos los pedidos que cargó Orders (el reporte saca los de prueba). */
  orders: Order[];
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

export function OrdersExportDialog({ open, onClose, orders, initialRange, activeDays, today }: Props) {
  if (!open) return null;
  return (
    <ExportBody onClose={onClose} orders={orders} initialRange={initialRange} activeDays={activeDays} today={today} />
  );
}

function ExportBody({ onClose, orders, initialRange, activeDays, today }: Omit<Props, "open">) {
  const titleId = useId();
  const [range, setRange] = useState<DateRange>(initialRange);
  const report = salesReport(orders, range);
  const rangeLabel = range.from ? describeDateRange(range, today) : "Todas las fechas";

  const loadedDays = orders.map(orderDayIso).filter(Boolean).sort();
  const oldestLoaded = loadedDays[0];
  const mayMissHistory = !!range.from && !!oldestLoaded && range.from < oldestLoaded;

  const download = () => {
    const name = range.from ? `ventas_${range.from}_${range.to ?? range.from}.csv` : "ventas_todas.csv";
    downloadText(name, salesReportCsv(report, orders, range));
  };

  return (
    <Modal open labelledBy={titleId} onClose={onClose} className="min-[760px]:max-w-[760px]!">
      <div className="flex items-start gap-3 border-b border-line bg-titlebar px-4 py-3">
        <div className="min-w-0">
          <h2 id={titleId} className="m-0 text-[15px] font-semibold">
            Reporte de ventas
          </h2>
          <p className="m-0 mt-0.5 text-xs text-fg-muted">{rangeLabel} · sin pedidos de prueba</p>
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
          <p role="note" className="m-0 rounded-lg border border-line px-3 py-2 text-xs text-fg-muted">
            Orders carga los pedidos más recientes: el más viejo cargado es del {oldestLoaded}. Antes de esa
            fecha puede faltar historia.
          </p>
        ) : null}

        <div className="grid grid-cols-2 gap-2 min-[560px]:grid-cols-3">
          <Metric label="Pedidos" value={report.orders} money={false} />
          <Metric label="Ventas" value={report.sales} />
          <Metric label="Ticket promedio" value={report.averageTicket} />
          <Metric label="Cobrado" value={report.collected} />
          <Metric label="Por cobrar" value={report.toCollect} />
          <Metric label="Envío incluido" value={report.shipping} />
          <Metric label="Contra entrega" value={report.byPayType.cod.sales} />
          <Metric label="Pago confirmado" value={report.byPayType.confirmed.sales} />
          <Metric label="Cancelados" value={report.cancelled.sales} />
        </div>

        <div className="grid gap-4 min-[560px]:grid-cols-2">
          <Breakdown
            title="Por etapa"
            rows={report.byStatus}
            label={(k) => ORDER_STATUS_META[k as OrderStatus]?.label ?? k}
          />
          <Breakdown title="Por día" rows={report.byDay} />
          <Breakdown title="Por canal" rows={report.byChannel} />
          <Breakdown title="Por ciudad" rows={report.byCity} />
        </div>
      </div>

      <div className="flex justify-end gap-2 border-t border-line px-4 py-3">
        <MacButton sm onClick={onClose}>
          Cerrar
        </MacButton>
        <MacButton sm primary onClick={download} disabled={report.orders === 0 && report.cancelled.orders === 0}>
          Descargar CSV
        </MacButton>
      </div>
    </Modal>
  );
}
