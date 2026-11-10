/**
 * Vista general de «Dónde compran»: TODAS las compras de la ventana por ciudad
 * y barrio, con las campañas que las trajeron (pedido del operador,
 * 2026-10-23).
 *
 * Incluye los pedidos sin conversación (tienda web, pedido a mano) en su
 * propio grupo. Una campaña que existe en la lista de Ads se abre con un clic
 * (la Page decide cuáles con `isSelectable` y qué hacer con `onSelectCampaign`).
 */
import { useState } from "react";

import {
  usePurchasePlaces,
  type AdsWindowParams,
  type PurchaseCity,
} from "@plugins/ads/frontend/entities/ads-campaign";
import { fmtMoney, fmtN } from "@plugins/ads/frontend/lib/format";

import { shareOf } from "../model/share";
import { Chevron, OrdersUnavailable, PlacesNotes, ShareBar, ZoneList } from "./parts";

interface Props {
  params: AdsWindowParams;
  onSelectCampaign?: (id: string) => void;
  isSelectable?: (id: string) => boolean;
}

export function PurchasePlacesOverview({ params, onSelectCampaign, isSelectable }: Props) {
  const { data, isLoading } = usePurchasePlaces(params, null);
  const [openKey, setOpenKey] = useState<string | null>(null);

  if (!data) {
    return (
      <p className="p-6 text-center text-[12.5px] text-fg-faint">
        {isLoading ? "Cargando compras…" : "No se pudieron cargar las compras."}
      </p>
    );
  }
  if (!data.ordersAvailable) return <OrdersUnavailable />;

  const tiles: { id: string; label: string; value: string }[] = [
    { id: "orders", label: "Compras", value: fmtN(data.orders) },
    { id: "paid", label: "Pagadas", value: fmtN(data.paidOrders) },
    { id: "value", label: "Valor", value: fmtMoney(data.valueCop) },
    { id: "cities", label: "Ciudades", value: fmtN(data.cities.length) },
  ];

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
      <p className="text-[11.5px] text-fg-muted">
        Pedidos de las fechas elegidas en el encabezado (por fecha del pedido),
        pagados o no, sin cancelados ni pruebas. Ciudad y barrio son los de la
        dirección de envío; incluye los pedidos que no vinieron de una
        conversación.
      </p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {tiles.map((t) => (
          <div
            key={t.id}
            data-testid={`places-total-${t.id}`}
            className="flex flex-col gap-0.5 rounded-md border border-line px-3 py-2"
          >
            <span className="text-[11px] uppercase tracking-wide text-fg-muted">{t.label}</span>
            <span className="text-sm font-semibold text-fg">{t.value}</span>
          </div>
        ))}
      </div>

      {data.cities.length === 0 ? (
        <p className="py-6 text-center text-[12.5px] text-fg-faint">
          Sin compras con ciudad de entrega en estas fechas.
        </p>
      ) : (
        <div className="att-tbl-wrap">
          <table className="att-tbl">
            <thead>
              <tr>
                <th>Ciudad</th>
                <th className="num">Compras</th>
                <th>Del total</th>
                <th className="num">Pagadas</th>
                <th className="num">Valor</th>
                <th>Campañas que las trajeron</th>
              </tr>
            </thead>
            <tbody>
              {data.cities.map((c) => {
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
                    <td className="num">{fmtN(c.orders)}</td>
                    <td className="min-w-36">
                      <ShareBar share={shareOf(c.orders, data.orders)} tone="accent" />
                    </td>
                    <td className="num">{fmtN(c.paidOrders)}</td>
                    <td className="num">{fmtMoney(c.valueCop)}</td>
                    <td>
                      <CampaignChips
                        city={c}
                        onSelectCampaign={onSelectCampaign}
                        isSelectable={isSelectable}
                      />
                    </td>
                  </tr>,
                  open && (
                    <tr key={`${c.key}-zones`}>
                      <td colSpan={6}>
                        <ZoneList cityKey={c.key} zones={c.zones} cityOrders={c.orders} />
                      </td>
                    </tr>
                  ),
                ];
              })}
            </tbody>
          </table>
        </div>
      )}

      <PlacesNotes places={data} />
    </div>
  );
}

function CampaignChips({
  city,
  onSelectCampaign,
  isSelectable,
}: {
  city: PurchaseCity;
  onSelectCampaign?: (id: string) => void;
  isSelectable?: (id: string) => boolean;
}) {
  return (
    <div className="flex flex-wrap gap-1">
      {city.campaigns.map((k) => {
        const body = (
          <>
            <span className="max-w-56 truncate">{k.name ?? k.id}</span>
            <span className="tabular-nums text-fg-faint">{fmtN(k.orders)}</span>
          </>
        );
        const chip =
          "inline-flex items-center gap-1.5 rounded-full border border-line px-2 py-0.5 text-[11px] text-fg-soft";
        return onSelectCampaign && isSelectable?.(k.id) ? (
          <button
            key={k.id}
            type="button"
            title="Abrir esta campaña"
            onClick={() => onSelectCampaign(k.id)}
            className={chip + " hover:border-accent hover:text-fg"}
          >
            {body}
          </button>
        ) : (
          <span key={k.id} className={chip}>
            {body}
          </span>
        );
      })}
    </div>
  );
}
