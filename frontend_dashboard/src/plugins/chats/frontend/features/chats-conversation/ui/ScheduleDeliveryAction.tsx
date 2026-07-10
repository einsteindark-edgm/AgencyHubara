import { useReducer, useState } from "react";
import {
  useOrderRefDetail,
  useScheduleOrder,
} from "@plugins/chats/frontend/entities/order-ref";
import { addDaysIso, formatIsoDateEs, todayIso } from "@/shared/lib";

interface Props {
  /** Id backend (Medusa) del pedido a agendar — `session.pending_payment_order_id`. */
  orderId: string;
  /** Popover abierto — estado elevado al composer (PM-007: un popover a la vez). */
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Acción rápida "Asignar fecha" en el composer del chat (modo intervenido).
 *
 * Hermana de `ConfirmPaymentAction`, pero de UN solo paso: agenda la entrega
 * (`PATCH /order-actions/{id}/schedule`, el mismo endpoint que usa el panel
 * "Lista para envío" del tablero de orders) SIN confirmar el pago. En el
 * backend eso convierte el draft → Order real y transiciona `new → preparing`;
 * sobre un pedido ya agendado solo actualiza la fecha (idempotente en stage).
 *
 * Como el pago queda pendiente, la sesión sigue exponiendo
 * `pending_payment_order_id` y ambos botones permanecen montados — el operador
 * puede confirmar el pago después con "Confirmar pago".
 */
type FlowState =
  | { phase: "idle" }
  | { phase: "scheduling" }
  | { phase: "error"; message: string };

type FlowAction =
  | { type: "submit" }
  | { type: "settle" }
  | { type: "fail"; message: string };

function flowReducer(_state: FlowState, action: FlowAction): FlowState {
  switch (action.type) {
    case "submit":
      return { phase: "scheduling" };
    case "settle":
      return { phase: "idle" };
    case "fail":
      return { phase: "error", message: action.message };
  }
}

const FLOW_LABEL: Record<FlowState["phase"], string> = {
  idle: "Asignar fecha",
  scheduling: "Agendando…",
  error: "Asignar fecha",
};

export function ScheduleDeliveryAction({ orderId, open, onOpenChange }: Props) {
  const schedule = useScheduleOrder();
  // Lazy (PM-006): fetch al abrir. Muestra la fecha ya agendada si existe
  // (PM-008) — abrir "para verificar" no debe parecer que no hay fecha.
  const detail = useOrderRefDetail(orderId, { enabled: open });
  const currentIso = detail.data?.summary?.due_iso ?? null;
  // Lazy init: "mañana" se calcula al montar, no al cargar el módulo — un
  // module-const quedaba stale si el dashboard pasaba la medianoche abierto.
  const [date, setDate] = useState(() => addDaysIso(1));
  const [time, setTime] = useState("");
  const [flow, dispatch] = useReducer(flowReducer, { phase: "idle" });

  const busy = flow.phase === "scheduling";
  const err = flow.phase === "error" ? flow.message : null;

  const submit = async () => {
    if (busy || !date) return;
    dispatch({ type: "submit" });
    try {
      const sr = await schedule.mutateAsync({
        orderId,
        delivery_iso: date,
        delivery_time: time || undefined,
      });
      if (!sr.success) {
        dispatch({
          type: "fail",
          message: sr.error_detail ?? "No se pudo agendar la entrega.",
        });
        return;
      }
      dispatch({ type: "settle" });
      onOpenChange(false);
    } catch (e) {
      dispatch({
        type: "fail",
        message: e instanceof Error ? e.message : String(e),
      });
    }
  };

  return (
    <span style={{ position: "relative", display: "inline-flex" }}>
      <button
        type="button"
        className="asignar-fecha-btn"
        onClick={() => onOpenChange(!open)}
        title="Agendar la fecha de entrega de este pedido (sin confirmar el pago)"
        style={scheduleBtnStyle}
      >
        📅 Asignar fecha
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Asignar fecha"
          style={popoverStyle}
          onClick={(e) => e.stopPropagation()}
        >
          <div style={{ fontWeight: 700, fontSize: "0.82rem", marginBottom: 2 }}>
            Agendar entrega del pedido
          </div>
          <p style={{ margin: 0, fontSize: "0.72rem", color: "var(--fg-faint, var(--color-neutral))", lineHeight: 1.35 }}>
            El pedido se agenda (queda como pedido en preparación) y el cliente
            recibe la notificación de entrega por WhatsApp. El pago NO se
            confirma — podés confirmarlo después.
          </p>
          {currentIso && (
            <p style={{ margin: 0, fontSize: "0.72rem", color: "var(--fg-faint, var(--color-neutral))", lineHeight: 1.35 }}>
              Actualmente agendada para <b>{formatIsoDateEs(currentIso)}</b> —
              esta acción la reemplaza.
            </p>
          )}

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
              onClick={() => onOpenChange(false)}
              disabled={busy}
              style={cancelStyle}
            >
              Cancelar
            </button>
            <button
              type="button"
              onClick={submit}
              disabled={busy || !date}
              style={{ ...scheduleBtnStyle, marginRight: 0, opacity: busy || !date ? 0.65 : 1 }}
            >
              {FLOW_LABEL[flow.phase]}
            </button>
          </div>
        </div>
      )}
    </span>
  );
}

/* ── styles (inline para no tocar el index.css spinal — mismo criterio que
   ConfirmPaymentAction y el resto del composer) ── */

const scheduleBtnStyle: React.CSSProperties = {
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
  background: "#2563eb",
  boxShadow: "0 0 0 2px rgba(37,99,235,0.35)",
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
  color: "var(--fg-faint, var(--color-neutral))",
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
  color: "var(--color-danger)",
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
