import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useConfirmOrderPayment,
  useScheduleOrder,
} from "@plugins/chats/frontend/entities/order-ref";
import { sessionKeys } from "@plugins/chats/frontend/entities/session";
import { addDaysIso, todayIso } from "@/shared/lib";

interface Props {
  /** Id backend (Medusa) del pedido a confirmar — `session.pending_payment_order_id`. */
  orderId: string;
}

/**
 * Acción rápida "Confirmar pago" en el composer del chat (modo intervenido).
 *
 * El padre solo la monta cuando la sesión tiene un pedido esperando
 * confirmación de pago (`session.pending_payment_order_id`).
 *
 * Por qué un popover y no un click directo: los pedidos que cierra el bot se
 * registran como **draft orders** de Medusa, y `confirm-payment` rechaza drafts
 * (`invalid_state: el pedido aún es draft. Agendá la entrega primero`). El
 * backend convierte draft→Order recién al **agendar la entrega**
 * (`schedule_delivery` → `convert-to-order`). Así que el flujo de 1 paso para
 * el humano es, internamente, dos llamadas secuenciales — las MISMAS que usa el
 * tablero de orders (`ReadyForShip` + el botón Confirmar pago del inspector):
 *
 *   1. `PATCH /orders/{id}/schedule {delivery_iso, delivery_time?}` → draft pasa
 *      a Order real + stage `new → preparing`.
 *   2. `PATCH /orders/{id}/confirm-payment` → marca el pago confirmado; el
 *      backend reescribe el `metadata.json` del chat (tag → COMPRA_EXITOSA) →
 *      la sesión deja de exponer `pending_payment_order_id` → el botón se
 *      desmonta solo (vía SSE; además invalidamos `sessionKeys` para que sea
 *      inmediato).
 *
 * Si el pedido YA es una Order real (no draft), igual agendamos primero: el
 * backend es idempotente (`schedule` sobre algo ya agendado devuelve success
 * sin reconvertir), así garantizamos el pre-requisito sin chequear estado acá.
 */
export function ConfirmPaymentAction({ orderId }: Props) {
  const qc = useQueryClient();
  const schedule = useScheduleOrder();
  const confirm = useConfirmOrderPayment();
  const [open, setOpen] = useState(false);
  // Lazy init: "mañana" se calcula al montar, no al cargar el módulo — un
  // module-const quedaba stale si el dashboard pasaba la medianoche abierto.
  const [date, setDate] = useState(() => addDaysIso(1));
  const [time, setTime] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const busy = schedule.isPending || confirm.isPending;
  const phase = schedule.isPending
    ? "Agendando…"
    : confirm.isPending
      ? "Confirmando pago…"
      : "Confirmar pago";

  const submit = () => {
    if (busy || !date) return;
    setErr(null);
    // Paso 1: agendar (convierte draft → Order real).
    schedule.mutate(
      { orderId, delivery_iso: date, delivery_time: time || undefined },
      {
        onSuccess: (sr) => {
          if (!sr.success) {
            setErr(sr.error_detail ?? "No se pudo agendar la entrega.");
            return;
          }
          // Paso 2: confirmar el pago sobre la Order ya convertida.
          confirm.mutate(
            { orderId },
            {
              onSuccess: (cr) => {
                if (cr.success) {
                  qc.invalidateQueries({ queryKey: sessionKeys.all });
                  setOpen(false);
                } else {
                  setErr(cr.error_detail ?? "No se pudo confirmar el pago.");
                }
              },
              onError: (e) => setErr(e.message),
            },
          );
        },
        onError: (e) => setErr(e.message),
      },
    );
  };

  return (
    <span style={{ position: "relative", display: "inline-flex" }}>
      <button
        type="button"
        className="confirm-pago-btn"
        onClick={() => setOpen((v) => !v)}
        title="Agendar la entrega y confirmar el pago de este pedido"
        style={confirmBtnStyle}
      >
        💳 Confirmar pago
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Confirmar pago"
          style={popoverStyle}
          onClick={(e) => e.stopPropagation()}
        >
          <div style={{ fontWeight: 700, fontSize: "0.82rem", marginBottom: 2 }}>
            Confirmar pago del pedido
          </div>
          <p style={{ margin: 0, fontSize: "0.72rem", color: "var(--fg-faint, #8e8e93)", lineHeight: 1.35 }}>
            El pedido se agenda (queda como pedido en preparación) y se marca el
            pago como recibido, en un solo paso.
          </p>

          <label style={fieldStyle}>
            <span style={lblStyle}>Fecha de entrega</span>
            <input
              type="date"
              value={date}
              min={todayIso()}
              onChange={(e) => setDate(e.target.value)}
              style={inputStyle}
            />
          </label>
          <label style={fieldStyle}>
            <span style={lblStyle}>Hora (opcional)</span>
            <input
              type="time"
              value={time}
              onChange={(e) => setTime(e.target.value)}
              style={inputStyle}
            />
          </label>

          {err && (
            <div role="alert" style={errStyle}>
              {err}
            </div>
          )}

          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4 }}>
            <button
              type="button"
              onClick={() => setOpen(false)}
              disabled={busy}
              style={cancelStyle}
            >
              Cancelar
            </button>
            <button
              type="button"
              onClick={submit}
              disabled={busy || !date}
              style={{ ...confirmBtnStyle, marginRight: 0, opacity: busy || !date ? 0.65 : 1 }}
            >
              {phase}
            </button>
          </div>
        </div>
      )}
    </span>
  );
}

/* ── styles (inline para no tocar el index.css spinal — mismo criterio que
   el resto del composer, que ya usa estilos inline / clases existentes) ── */

const confirmBtnStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "0.35rem",
  marginRight: "0.5rem",
  padding: "0.35rem 0.7rem",
  borderRadius: "8px",
  border: "none",
  fontSize: "0.78rem",
  fontWeight: 700,
  color: "#fff",
  background: "#16a34a",
  boxShadow: "0 0 0 2px rgba(22,163,74,0.35)",
  cursor: "pointer",
  whiteSpace: "nowrap",
};

const popoverStyle: React.CSSProperties = {
  position: "absolute",
  bottom: "calc(100% + 8px)",
  right: 0,
  width: 280,
  display: "flex",
  flexDirection: "column",
  gap: 8,
  padding: "0.8rem",
  borderRadius: 10,
  background: "var(--bg-elev, #1c1c1e)",
  border: "1px solid var(--border, rgba(255,255,255,0.12))",
  boxShadow: "0 8px 28px rgba(0,0,0,0.45)",
  zIndex: 30,
};

const fieldStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 3,
};
const lblStyle: React.CSSProperties = {
  fontSize: "0.68rem",
  color: "var(--fg-faint, #8e8e93)",
};
const inputStyle: React.CSSProperties = {
  padding: "0.35rem 0.5rem",
  borderRadius: 6,
  border: "1px solid var(--border, rgba(255,255,255,0.15))",
  background: "var(--bg, #2c2c2e)",
  color: "inherit",
  fontSize: "0.78rem",
};
const errStyle: React.CSSProperties = {
  padding: "0.4rem 0.55rem",
  borderRadius: 6,
  background: "rgba(255,114,105,0.14)",
  border: "1px solid rgba(255,114,105,0.4)",
  color: "#ff7269",
  fontSize: "0.7rem",
  lineHeight: 1.3,
};
const cancelStyle: React.CSSProperties = {
  padding: "0.35rem 0.7rem",
  borderRadius: 8,
  border: "1px solid var(--border, rgba(255,255,255,0.15))",
  background: "transparent",
  color: "inherit",
  fontSize: "0.78rem",
  cursor: "pointer",
};
