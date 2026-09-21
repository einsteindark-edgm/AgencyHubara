/**
 * Modal de salto de etapas — aparece al soltar un pedido saltándose columnas
 * (ej. En preparación → Entregada porque el operador olvidó moverlo).
 *
 * El aviso al cliente viene APAGADO: si el pedido ya se entregó, un "ya va en
 * camino" a destiempo confunde. Meta (CAPI) se entera igual de la etapa final;
 * el historial guarda UNA entrada con las etapas omitidas.
 *
 * Overlay propio (regla #6: cero diálogos JS nativos), mismo patrón que
 * `TrackingLinkModal`: backdrop + `role="dialog"` + Escape.
 */
import { useEffect, useState } from "react";
import {
  ORDER_STATUS_META,
  type OrderStatus,
} from "@plugins/orders/frontend/entities/order";
import { MacButton } from "@/shared/ui";

interface Props {
  /** Id visible del pedido (`#1247`) — solo para el título. */
  orderId: string;
  from: OrderStatus;
  to: OrderStatus;
  /** Etapas intermedias que se omiten (de `skippedStages`). */
  skipped: OrderStatus[];
  /** Transición en vuelo: deshabilita las acciones. */
  busy?: boolean;
  onConfirm: (notifyCustomer: boolean) => void;
  onCancel: () => void;
}

export function SkipStagesModal({
  orderId,
  from,
  to,
  skipped,
  busy = false,
  onConfirm,
  onCancel,
}: Props) {
  const [notify, setNotify] = useState(false);

  // Escape cierra — listener global mientras el modal está montado.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const label = (s: OrderStatus) => ORDER_STATUS_META[s].label;
  const toLabel = label(to);

  return (
    <div
      data-testid="skip-stages-backdrop"
      onClick={onCancel}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Mover el pedido ${orderId} a ${toLabel}`}
        onClick={(e) => e.stopPropagation()}
        className="flex w-[440px] max-w-full flex-col gap-3 rounded-xl border border-line bg-canvas p-4 shadow-2xl"
      >
        <div>
          <div className="text-[13px] font-bold text-fg">
            ⏭ Saltar etapas · {orderId}
          </div>
          <p className="mt-1 text-[11.5px] leading-snug text-fg-faint">
            El pedido pasa de <b>{label(from)}</b> a <b>{toLabel}</b> sin pasar
            por <b>{skipped.map(label).join(", ")}</b>. Queda anotado en el
            historial del pedido.
          </p>
        </div>

        <label className="flex items-start gap-2 text-[12px] text-fg">
          <input
            type="checkbox"
            checked={notify}
            disabled={busy}
            onChange={(e) => setNotify(e.target.checked)}
            className="mt-0.5"
          />
          <span>
            Avisar al cliente por WhatsApp
            <span className="block text-[11px] text-fg-faint">
              Dejalo apagado si el pedido ya se entregó: el aviso llegaría a destiempo.
            </span>
          </span>
        </label>

        <div className="flex items-center justify-end gap-2">
          <MacButton ghost sm type="button" onClick={onCancel} disabled={busy}>
            Cancelar
          </MacButton>
          <MacButton primary sm type="button" onClick={() => onConfirm(notify)} disabled={busy}>
            Mover a {toLabel}
          </MacButton>
        </div>
      </div>
    </div>
  );
}
