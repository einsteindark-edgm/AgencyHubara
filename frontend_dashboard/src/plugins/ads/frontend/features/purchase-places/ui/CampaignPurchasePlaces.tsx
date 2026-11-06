/**
 * «Dónde compran» de la fila activa de Ads (campaña, segmento o anuncio).
 *
 * Pedido del operador (2026-10-23): ver en qué ciudades y zonas compran por
 * campaña y poder cotejarlo. Cada ciudad trae dos barras: su peso en las
 * compras de ESTA campaña y en TODAS las compras de la ventana — si una
 * campaña vende en Cali el doble de lo que pesa Cali en el total, se ve acá.
 * Al abrir una ciudad, sus barrios.
 *
 * Ciudad y barrio son los de la dirección de envío del pedido en Orders; la
 * ventana es la del header, por fecha del pedido.
 */
import { useState } from "react";

import {
  usePurchasePlaces,
  type AdsWindowParams,
} from "@plugins/ads/frontend/entities/ads-campaign";
import { fmtMoney } from "@plugins/ads/frontend/lib/format";

import { purchases, shareOf } from "../model/share";
import { Chevron, OrdersUnavailable, PlacesNotes, ShareBar, ZoneList } from "./parts";

/** Ciudades visibles antes de "ver todas". */
const TOP_CITIES = 8;

interface Props {
  params: AdsWindowParams;
  campaignId: string;
  adsetId: string | null;
  /** Abre la vista general (todas las compras). */
  onOpenOverview?: () => void;
}

export function CampaignPurchasePlaces({ params, campaignId, adsetId, onOpenOverview }: Props) {
  const { data } = usePurchasePlaces(params, campaignId, adsetId);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const cities = data?.cities ?? [];
  const visible = showAll ? cities : cities.slice(0, TOP_CITIES);

  return (
    <section className="ads-card" aria-label="Dónde compran">
      <header className="ads-card-h">
        <div>
          <h3>Dónde compran</h3>
          <p>
            Ciudad y barrio de entrega de las compras de esta campaña, frente a
            todas las compras de las mismas fechas
          </p>
        </div>
        {data && data.ordersAvailable && (
          <span
            className="ads-card-meta"
            title="Valor de los pedidos, pagados o no (los ingresos atribuidos cuentan solo los pagados)"
          >
            {purchases(data.orders)} · {data.paidOrders} pagadas · {fmtMoney(data.valueCop)}
          </span>
        )}
        {onOpenOverview && (
          <button
            type="button"
            onClick={onOpenOverview}
            className={
              "shrink-0 rounded-md border border-line-strong px-2.5 py-1 text-[11.5px] " +
              "font-semibold text-fg-soft hover:bg-line hover:text-fg" +
              (data?.ordersAvailable ? "" : " ml-auto")
            }
          >
            Ver todas las compras
          </button>
        )}
      </header>

      {data && !data.ordersAvailable && <OrdersUnavailable />}

      {data && data.ordersAvailable && cities.length === 0 && (
        <p className="py-6 text-center text-[12.5px] text-fg-faint">
          {data.orders > 0
            ? "Ningún pedido de esta campaña trae ciudad de entrega."
            : "Esta campaña está sin compras en estas fechas."}
        </p>
      )}

      {data && data.ordersAvailable && cities.length > 0 && (
        <div className="att-tbl-wrap">
          <table className="att-tbl">
            <thead>
              <tr>
                <th>Ciudad</th>
                <th>En esta campaña</th>
                <th title="Peso de la ciudad en todas las compras de las mismas fechas">
                  En todas las compras
                </th>
                <th className="num">Compras</th>
                <th className="num">Valor</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((c) => {
                const open = openKey === c.key;
                return [
                  <tr key={c.key} data-testid={`place-city-${c.key}`}>
                    <td>
                      <button
                        type="button"
                        aria-expanded={open}
                        onClick={() => setOpenKey(open ? null : c.key)}
                        className="flex items-center gap-1 text-left text-[12.5px] font-semibold text-fg hover:text-accent"
                      >
                        <Chevron open={open} />
                        {c.city}
                      </button>
                    </td>
                    <td className="min-w-40">
                      <ShareBar share={shareOf(c.orders, data.orders)} tone="accent" />
                    </td>
                    <td className="min-w-40">
                      <ShareBar
                        share={shareOf(c.overallOrders ?? 0, data.overallOrders)}
                        tone="neutral"
                      />
                    </td>
                    <td className="num">{purchases(c.orders)}</td>
                    <td className="num">{fmtMoney(c.valueCop)}</td>
                  </tr>,
                  open && (
                    <tr key={`${c.key}-zones`}>
                      <td colSpan={5}>
                        <ZoneList cityKey={c.key} zones={c.zones} cityOrders={c.orders} />
                      </td>
                    </tr>
                  ),
                ];
              })}
            </tbody>
          </table>
          {cities.length > TOP_CITIES && (
            <button
              type="button"
              onClick={() => setShowAll(!showAll)}
              className="pt-2 text-[11.5px] font-semibold text-accent hover:underline"
            >
              {showAll ? "Ver menos" : `Ver las ${cities.length} ciudades`}
            </button>
          )}
        </div>
      )}

      {data && data.ordersAvailable && <PlacesNotes places={data} />}
    </section>
  );
}
