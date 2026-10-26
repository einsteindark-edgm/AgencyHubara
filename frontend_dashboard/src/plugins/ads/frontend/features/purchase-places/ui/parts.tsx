/**
 * Piezas compartidas por las dos vistas de «Dónde compran» (misma feature):
 * barra de participación, barrios de una ciudad y avisos al pie.
 */
import type {
  PurchasePlaces,
  PurchaseZone,
} from "@plugins/ads/frontend/entities/ads-campaign";
import { fmtMoney } from "@plugins/ads/frontend/lib/format";

import { fmtShare, purchases, shareOf } from "../model/share";

/** Barra horizontal + porcentaje. Tonos estáticos (Tailwind no genera
 *  clases dinámicas). */
export function ShareBar({ share, tone }: { share: number | null; tone: "accent" | "neutral" }) {
  const width = share === null ? 0 : Math.max(2, Math.round(share * 100));
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 min-w-16 flex-1 overflow-hidden rounded-full bg-line">
        <div
          className={"h-full rounded-full " + (tone === "accent" ? "bg-accent" : "bg-fg-faint")}
          style={{ width: `${width}%` }}
        />
      </div>
      <span className="w-10 text-right text-[11.5px] tabular-nums text-fg-muted">
        {fmtShare(share)}
      </span>
    </div>
  );
}

/** Flecha del acordeón: decorativa (no entra al nombre del botón). */
export function Chevron({ open }: { open: boolean }) {
  return (
    <span aria-hidden className="inline-block w-3 text-fg-faint">
      {open ? "▾" : "▸"}
    </span>
  );
}

/** Los barrios de una ciudad, de más a menos compras. */
export function ZoneList({
  cityKey,
  zones,
  cityOrders,
}: {
  cityKey: string;
  zones: PurchaseZone[];
  cityOrders: number;
}) {
  return (
    <ul
      data-testid={`place-zones-${cityKey}`}
      className="grid grid-cols-1 gap-x-6 gap-y-1 py-1 pl-5 sm:grid-cols-2"
    >
      {zones.map((z) => (
        <li
          key={z.zone ?? ""}
          className="grid grid-cols-[minmax(0,1fr)_auto_auto_auto] items-center gap-3 whitespace-nowrap text-[12px]"
        >
          <span className={"truncate " + (z.zone ? "text-fg-soft" : "italic text-fg-faint")}>
            {z.zone ?? "Sin barrio"}
          </span>
          <span className="tabular-nums text-fg-muted">{purchases(z.orders)}</span>
          <span className="text-right tabular-nums text-fg-faint">{fmtMoney(z.valueCop)}</span>
          <span
            className="w-10 text-right tabular-nums text-fg-faint"
            title="Parte de las compras de la ciudad"
          >
            {fmtShare(shareOf(z.orders, cityOrders))}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Lo que el listado no muestra: compras sin ciudad y el tope de pedidos. */
export function PlacesNotes({ places }: { places: PurchasePlaces }) {
  if (!places.withoutCity && !places.truncated) return null;
  return (
    <div className="flex flex-col gap-0.5 pt-2 text-[11px] text-fg-faint">
      {places.withoutCity > 0 && (
        <p>
          Sin ciudad en el pedido: {purchases(places.withoutCity)} (en el total,
          no en la lista).
        </p>
      )}
      {places.truncated && (
        <p>
          Hay más pedidos de los que se leen por consulta: con un rango de
          fechas más corto se ven todos.
        </p>
      )}
    </div>
  );
}

/** Orders no respondió: no se sabe (≠ cero compras). */
export function OrdersUnavailable() {
  return (
    <p className="py-6 text-center text-[12.5px] text-fg-muted">
      Orders no respondió: ahora no se puede saber dónde compran. Se actualiza
      solo cuando Orders vuelva.
    </p>
  );
}
