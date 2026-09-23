/**
 * Paso "Despachar" del panel móvil — espejo del modal "En camino" del tablero.
 *
 * Desglose del cobro: el valor del pedido (solo lectura), el valor del envío
 * que escribe el operador y el total en vivo. El valor del envío y el link de
 * la guía (ambos opcionales) viajan en el mismo cambio de estado; el agente
 * ETA los detalla en el WhatsApp de "ya va en camino". Mismos validadores que
 * el escritorio (`@/shared/lib`), espejo de los del backend.
 */
import { useState } from "react";

import { fmtMoney, normalizeTrackingUrl, parseShippingCost } from "@/shared/lib";

export interface ShipExtras {
  shipping_cost?: number;
  tracking_url?: string;
}

interface Props {
  /** Total del pedido en COP (sin envío). `null` = desconocido. */
  orderTotal: number | null;
  busy: boolean;
  onConfirm: (extras: ShipExtras) => void;
  onCancel: () => void;
}

export function ShipStep({ orderTotal, busy, onConfirm, onCancel }: Props) {
  const [link, setLink] = useState("");
  const [cost, setCost] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = (withLink: boolean) => {
    if (busy) return;
    const extras: ShipExtras = {};
    if (withLink) {
      const url = normalizeTrackingUrl(link);
      if (!url) {
        setError(
          "Ese link no sirve: pega la URL completa de rastreo (empieza con https://), sin espacios.",
        );
        return;
      }
      extras.tracking_url = url;
    }
    const parsed = parseShippingCost(cost);
    if ("error" in parsed) {
      setError(parsed.error);
      return;
    }
    if (parsed.value != null) extras.shipping_cost = parsed.value;
    setError(null);
    onConfirm(extras);
  };

  const parsedCost = parseShippingCost(cost);
  const shippingValue = "value" in parsedCost ? (parsedCost.value ?? 0) : 0;
  const knownTotal = orderTotal != null && orderTotal > 0 ? orderTotal : null;
  const hasLink = link.trim() !== "";

  return (
    <div className="order-step" aria-label="Despachar">
      <p className="order-step-hint">
        El cliente recibe por WhatsApp el aviso de que su pedido va en camino.
        Con el valor del envío le detallamos pedido, envío y total; el link de
        la guía va al final. Los dos son opcionales.
      </p>

      {knownTotal != null && (
        <div className="order-step-row">
          <span>Valor del pedido</span>
          <span>{fmtMoney(knownTotal)}</span>
        </div>
      )}
      <label className="order-step-field">
        <span>Valor del envío (opcional)</span>
        <input
          type="text"
          inputMode="numeric"
          placeholder="12.000"
          value={cost}
          disabled={busy}
          onChange={(e) => {
            setCost(e.target.value);
            if (error) setError(null);
          }}
        />
      </label>
      {knownTotal != null && (
        <div className="order-step-row order-step-total">
          <span>Total</span>
          <span data-testid="ship-total">{fmtMoney(knownTotal + shippingValue)}</span>
        </div>
      )}
      <label className="order-step-field">
        <span>Link de la guía (opcional)</span>
        <input
          type="url"
          inputMode="url"
          placeholder="https://www.servientrega.com/…?guia=123456"
          value={link}
          disabled={busy}
          onChange={(e) => {
            setLink(e.target.value);
            if (error) setError(null);
          }}
        />
      </label>

      {error && (
        <div className="order-card-err" role="alert">
          {error}
        </div>
      )}

      <div className="order-card-actions">
        <button
          className="order-advance-btn"
          disabled={busy || !hasLink}
          onClick={() => submit(true)}
        >
          Despachar con guía
        </button>
        <button className="order-ghost-btn" disabled={busy} onClick={() => submit(false)}>
          Despachar sin guía
        </button>
        <button className="order-ghost-btn" disabled={busy} onClick={onCancel}>
          Volver
        </button>
      </div>
    </div>
  );
}
