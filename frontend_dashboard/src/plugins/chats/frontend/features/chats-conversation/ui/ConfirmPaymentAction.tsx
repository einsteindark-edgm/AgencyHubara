import { useReducer, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useConfirmOrderPayment,
  useOrderRefDetail,
  useScheduleOrder,
} from "@plugins/chats/frontend/entities/order-ref";
import { sessionKeys } from "@plugins/chats/frontend/entities/session";
import { addDaysIso, formatIsoDateEs, todayIso } from "@/shared/lib";

interface Props {
  /** Id backend (Medusa) del pedido a confirmar — `session.pending_payment_order_id`. */
  orderId: string;
  /** Popover abierto — estado elevado al composer (PM-007: un popover a la vez). */
  open: boolean;
  onOpenChange: (open: boolean) => void;
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
 * Si el pedido YA tiene fecha de entrega asignada (`summary.due_iso` del
 * read-side del cast — puesta por "Asignar fecha" en el chat o por el tablero
 * de orders), el paso 1 se SALTA: re-agendar acá pisaría la fecha del operador
 * con el default del popover. En ese caso el popover muestra la fecha agendada
 * y el submit hace solo confirm-payment (el pedido ya fue convertido a Order
 * real al agendarse). Sin fecha asignada — o si el read-side no responde — se
 * mantiene el flujo de 2 pasos (schedule es idempotente sobre stage).
 */
/**
 * F5.3 (auditoría 2026-06-10): el flujo de 2 pasos se modela con una unión
 * discriminada + reducer — estados imposibles ("agendando" y "error" a la
 * vez) quedan irrepresentables. Este es el patrón de referencia para flujos
 * multi-paso del dashboard (CLAUDE.md frontend §estado, regla 3).
 */
type FlowState =
  | { phase: "idle" }
  | { phase: "scheduling" }
  | { phase: "confirming" }
  | { phase: "error"; message: string };

type FlowAction =
  | { type: "submit" }
  | { type: "advance" }
  | { type: "settle" }
  | { type: "fail"; message: string };

function flowReducer(_state: FlowState, action: FlowAction): FlowState {
  switch (action.type) {
    case "submit":
      return { phase: "scheduling" };
    case "advance":
      return { phase: "confirming" };
    case "settle":
      return { phase: "idle" };
    case "fail":
      return { phase: "error", message: action.message };
  }
}

const FLOW_LABEL: Record<FlowState["phase"], string> = {
  idle: "Confirmar pago",
  scheduling: "Agendando…",
  confirming: "Confirmando pago…",
  error: "Confirmar pago",
};

export function ConfirmPaymentAction({ orderId, open, onOpenChange }: Props) {
  const qc = useQueryClient();
  const schedule = useScheduleOrder();
  const confirm = useConfirmOrderPayment();
  // Lazy: fetch al ABRIR el popover, no al montar el composer (PM-006) — y
  // cada apertura re-valida si la fecha apareció mientras tanto (PM-003).
  const detail = useOrderRefDetail(orderId, { enabled: open });
  // Lazy init: "mañana" se calcula al montar, no al cargar el módulo — un
  // module-const quedaba stale si el dashboard pasaba la medianoche abierto.
  const [date, setDate] = useState(() => addDaysIso(1));
  const [time, setTime] = useState("");
  const [flow, dispatch] = useReducer(flowReducer, { phase: "idle" });

  // Fecha YA asignada por el operador ("Asignar fecha" / tablero orders):
  // null = sin agendar (o read-side caído → fallback al flujo de 2 pasos).
  const scheduledIso = detail.data?.summary?.due_iso ?? null;
  const scheduledTime = detail.data?.summary?.due_time ?? null;

  const busy = flow.phase === "scheduling" || flow.phase === "confirming";
  // PM-001: mientras el read-side verifica si hay fecha, NO dejamos confirmar
  // — el snapshot viejo (due_iso=null) re-agendaría pisando la fecha real.
  const verifying = detail.isFetching;
  const err = flow.phase === "error" ? flow.message : null;

  const submit = async () => {
    if (busy || verifying || (!scheduledIso && !date)) return;
    dispatch({ type: "submit" });
    try {
      if (!scheduledIso) {
        // Paso 1: agendar (convierte draft → Order real). Se salta si la
        // entrega ya tiene fecha — re-agendar pisaría la fecha del operador.
        // El note deja rastro en el history del pedido de que este agendado
        // fue implícito (PM-010: distinguible de un "Asignar fecha" a mano).
        const sr = await schedule.mutateAsync({
          orderId,
          delivery_iso: date,
          delivery_time: time || undefined,
          note: "Agendado al confirmar pago desde el chat",
        });
        if (!sr.success) {
          dispatch({
            type: "fail",
            message: sr.error_detail ?? "No se pudo agendar la entrega.",
          });
          return;
        }
      }
      // Paso 2: confirmar el pago sobre la Order ya convertida.
      dispatch({ type: "advance" });
      const cr = await confirm.mutateAsync({ orderId });
      if (!cr.success) {
        dispatch({
          type: "fail",
          message: cr.error_detail ?? "No se pudo confirmar el pago.",
        });
        return;
      }
      qc.invalidateQueries({ queryKey: sessionKeys.all });
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
        className="confirm-pago-btn"
        onClick={() => onOpenChange(!open)}
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
          {detail.isError && (
            <div role="alert" style={warnStyle}>
              No se pudo verificar si ya hay fecha asignada — confirmar acá
              (re)agenda la entrega con la fecha de abajo.
            </div>
          )}
          {scheduledIso ? (
            <p style={{ margin: 0, fontSize: "0.72rem", color: "var(--fg-faint, var(--color-neutral))", lineHeight: 1.35 }}>
              Entrega ya agendada para{" "}
              <b>
                {formatIsoDateEs(scheduledIso)}
                {scheduledTime ? ` ${scheduledTime}` : ""}
              </b>
              . Solo se marca el pago como recibido — la fecha no se toca.
            </p>
          ) : (
            <>
              <p style={{ margin: 0, fontSize: "0.72rem", color: "var(--fg-faint, var(--color-neutral))", lineHeight: 1.35 }}>
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
            </>
          )}

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
              disabled={busy || verifying || (!scheduledIso && !date)}
              style={{ ...confirmBtnStyle, marginRight: 0, opacity: busy || verifying || (!scheduledIso && !date) ? 0.65 : 1 }}
            >
              {verifying ? "Verificando pedido…" : FLOW_LABEL[flow.phase]}
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
const warnStyle: React.CSSProperties = {
  padding: "0.4rem 0.55rem",
  borderRadius: 6,
  background: "rgba(255,180,60,0.14)",
  border: "1px solid rgba(255,180,60,0.4)",
  color: "var(--color-warning, #d97706)",
  fontSize: "0.7rem",
  lineHeight: 1.3,
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
