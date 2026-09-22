/**
 * Modal "En camino" — aparece al soltar un pedido en la columna `shipping`.
 *
 * El operador puede pegar el link de la guía de la transportadora y escribir
 * el valor del envío (ambos opcionales). Viajan en el mismo `PATCH .../stage`
 * (`tracking_url`, `shipping_cost`): el agente ETA escribe el valor en el
 * mensaje de WhatsApp que anuncia "ya va en camino" ("El valor del envío es
 * $ 12.000") y pone el link al final — como URL cruda en su propia burbuja,
 * para que WhatsApp la muestre como link tappable. Sin link ni valor, el
 * mensaje sale igual que siempre.
 *
 * Overlay propio (regla #6: cero diálogos JS nativos), mismo patrón que el
 * visor de audiencia de marketing: backdrop + `role="dialog"` + Escape.
 */
import { useEffect, useRef, useState } from "react";
import { MacButton } from "@/shared/ui";

import { parseShippingCost } from "../model/shippingCost";
import { normalizeTrackingUrl } from "../model/trackingUrl";

export interface ShippingConfirm {
  /** `null` = marcar en camino sin guía; string = link ya normalizado. */
  trackingUrl: string | null;
  /** `null` = sin valor de envío; number = COP entero. */
  shippingCost: number | null;
}

interface Props {
  /** Id visible del pedido (`#1247`) — solo para el título. */
  orderId: string;
  /** Transición en vuelo: deshabilita las acciones. */
  busy?: boolean;
  onConfirm: (result: ShippingConfirm) => void;
  onCancel: () => void;
}

export function TrackingLinkModal({ orderId, busy = false, onConfirm, onCancel }: Props) {
  const [value, setValue] = useState("");
  const [cost, setCost] = useState("");
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Escape cierra — listener global mientras el modal está montado.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  /** Valor del envío validado, o `undefined` si es inválido (ya pintó el error). */
  const shippingCost = (): number | null | undefined => {
    const parsed = parseShippingCost(cost);
    if ("error" in parsed) {
      setError(parsed.error);
      return undefined;
    }
    return parsed.value;
  };

  const submitWithoutLink = () => {
    if (busy) return;
    const amount = shippingCost();
    if (amount === undefined) return;
    setError(null);
    onConfirm({ trackingUrl: null, shippingCost: amount });
  };

  const submitWithLink = () => {
    if (busy) return;
    const url = normalizeTrackingUrl(value);
    if (!url) {
      setError(
        "Ese link no sirve: pegá la URL completa de rastreo (empieza con https://), sin espacios.",
      );
      return;
    }
    const amount = shippingCost();
    if (amount === undefined) return;
    setError(null);
    onConfirm({ trackingUrl: url, shippingCost: amount });
  };

  const hasText = value.trim() !== "";

  return (
    <div
      data-testid="tracking-link-backdrop"
      onClick={onCancel}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Marcar en camino el pedido ${orderId}`}
        onClick={(e) => e.stopPropagation()}
        className="flex w-[440px] max-w-full flex-col gap-3 rounded-xl border border-line bg-canvas p-4 shadow-2xl"
      >
        <div>
          <div className="text-[13px] font-bold text-fg">🚚 Marcar en camino · {orderId}</div>
          <p className="mt-1 text-[11.5px] leading-snug text-fg-faint">
            El cliente recibe por WhatsApp el aviso de que su pedido ya va en
            camino. Si escribís el valor del envío, va en el mensaje; si pegás
            el link de la guía, va al final como link para que lo abra con un
            toque. Los dos son opcionales.
          </p>
        </div>

        <label className="flex flex-col gap-1">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-fg-faint">
            Valor del envío (opcional)
          </span>
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
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                if (hasText) submitWithLink();
                else submitWithoutLink();
              }
            }}
            className="w-full rounded-md border border-line bg-transparent px-2 py-1.5 text-[12px] text-fg outline-none placeholder:text-fg-faint focus:border-accent"
          />
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-fg-faint">
            Link de la guía (opcional)
          </span>
          <input
            ref={inputRef}
            type="url"
            inputMode="url"
            placeholder="https://www.servientrega.com/…?guia=123456"
            value={value}
            disabled={busy}
            onChange={(e) => {
              setValue(e.target.value);
              if (error) setError(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                if (hasText) submitWithLink();
                else submitWithoutLink();
              }
            }}
            className="w-full rounded-md border border-line bg-transparent px-2 py-1.5 text-[12px] text-fg outline-none placeholder:text-fg-faint focus:border-accent"
          />
          {error && (
            <span role="alert" className="text-[11px] text-danger">
              {error}
            </span>
          )}
        </label>

        <div className="flex items-center justify-end gap-2">
          <MacButton ghost sm type="button" onClick={onCancel} disabled={busy}>
            Cancelar
          </MacButton>
          <MacButton sm type="button" onClick={submitWithoutLink} disabled={busy}>
            Marcar sin guía
          </MacButton>
          <MacButton primary sm type="button" onClick={submitWithLink} disabled={busy || !hasText}>
            Marcar con guía
          </MacButton>
        </div>
      </div>
    </div>
  );
}
