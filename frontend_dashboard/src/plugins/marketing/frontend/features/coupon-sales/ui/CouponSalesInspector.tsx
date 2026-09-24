/**
 * Inspector derecho de la vista Cupones — ventas del cupón: totales
 * (pedidos, descuento total en COP, unidades del cupo) y cada venta con su
 * número de pedido, fecha, unidades y descuento. El número va como TEXTO:
 * el pedido vive en otro plugin y marketing no enlaza a sus pantallas.
 * Un pedido nuevo o cancelado llega por el evento `orders` del stream.
 */

import {
  useCouponOrdersEvents,
  useCouponSales,
  type CouponSale,
} from "@plugins/marketing/frontend/entities/coupon";
import {
  apiErrorDetail,
  fmtCop,
  fmtDateTimeMs,
  fmtN,
} from "@plugins/marketing/frontend/lib/format";

export function CouponSalesInspector({ couponId }: { couponId: string }) {
  useCouponOrdersEvents();
  const { data, isPending, error } = useCouponSales(couponId);

  return (
    <aside className="inspector">
      <div className="flex h-[38px] shrink-0 items-center border-b border-line bg-sidebar px-3">
        <span className="text-[12px] font-semibold text-fg">Ventas con el cupón</span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {error ? (
          <p className="text-[11.5px] text-danger">{apiErrorDetail(error)}</p>
        ) : isPending || !data ? (
          <p className="text-[11.5px] text-fg-muted">Cargando ventas…</p>
        ) : (
          <div className="flex flex-col gap-3">
            <dl data-testid="coupon-sales-totals" className="grid grid-cols-3 gap-2">
              <Total label="Pedidos" value={fmtN(data.orders)} />
              <Total label="Descuento total" value={fmtCop(data.discountCop)} />
              <Total label="Unidades del cupo" value={fmtN(data.quotaUnits)} />
            </dl>

            {data.sales.length === 0 ? (
              <p className="text-[11.5px] text-fg-faint">Todavía no hay ventas con este cupón.</p>
            ) : (
              <table className="w-full border-collapse text-left text-[11.5px]">
                <thead>
                  <tr className="text-[10px] uppercase tracking-wide text-fg-faint">
                    <th className="py-1 pr-2 font-medium">Pedido</th>
                    <th className="py-1 pr-2 font-medium">Fecha</th>
                    <th className="py-1 pr-2 text-right font-medium">Unid.</th>
                    <th className="py-1 text-right font-medium">Descuento</th>
                  </tr>
                </thead>
                <tbody>
                  {data.sales.map((s) => (
                    <SaleRow key={s.orderId} sale={s} />
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}

function Total({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-md border border-line px-2 py-1.5">
      <dt className="text-[10px] leading-tight text-fg-muted">{label}</dt>
      <dd className="text-[13px] font-semibold tabular-nums text-fg">{value}</dd>
    </div>
  );
}

function SaleRow({ sale: s }: { sale: CouponSale }) {
  const ms = Date.parse(s.createdAt);
  return (
    <tr className="border-t border-line">
      <td className="py-1.5 pr-2 font-medium text-fg">
        {s.displayId !== null ? `#${s.displayId}` : null}
        {s.isDraft ? (
          <span className="ml-1 rounded bg-line/60 px-1 py-0.5 text-[9.5px] font-semibold text-fg-muted">
            Borrador
          </span>
        ) : null}
      </td>
      <td className="py-1.5 pr-2 tabular-nums text-fg-muted">
        {Number.isNaN(ms) ? s.createdAt : fmtDateTimeMs(ms)}
      </td>
      <td className="py-1.5 pr-2 text-right tabular-nums text-fg">{fmtN(s.quotaUnits)}</td>
      <td className="py-1.5 text-right tabular-nums text-fg">{fmtCop(s.discountCop)}</td>
    </tr>
  );
}
