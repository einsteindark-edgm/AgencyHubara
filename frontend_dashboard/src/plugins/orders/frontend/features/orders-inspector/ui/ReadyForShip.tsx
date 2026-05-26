/**
 * "Lista para envío" — único panel de agendamiento del inspector.
 *
 * Llama `useScheduleOrder` que persiste a Medusa via merge-patch metadata.
 * Al guardar, la orden transiciona de `new` → `preparing` (atómico en el
 * backend), la card se mueve a la columna "En preparación" en el kanban
 * y este panel desaparece (no se renderiza para orders ya agendadas).
 *
 * El componente se MERGEÓ con el antiguo `SchedulePanel` (2026-05-26)
 * porque tener dos formularios de "agendar" en el mismo inspector
 * confundía al operador.
 */

import { useState } from "react";
import { useScheduleOrder, type Order } from "@/entities/order";
import { Icon, MacButton } from "@/shared/ui";

interface Props {
  order: Order;
}

export function ReadyForShip({ order }: Props) {
  // Default = mañana en formato YYYY-MM-DD
  const tomorrow = new Date(Date.now() + 86_400_000)
    .toISOString()
    .slice(0, 10);
  const todayIso = new Date().toISOString().slice(0, 10);

  const [date, setDate] = useState<string>(order.dueIso || tomorrow);
  const [time, setTime] = useState<string>(
    order.dueTime && order.dueTime !== "—" ? order.dueTime : "",
  );
  const [note, setNote] = useState<string>("");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const schedule = useScheduleOrder();

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);
    schedule.mutate(
      {
        orderId: order.id,
        delivery_iso: date,
        delivery_time: time || undefined,
        note: note || undefined,
      },
      {
        onSuccess: (r) => {
          if (!r.success && r.error_detail) setErrorMsg(r.error_detail);
        },
        onError: (err) => setErrorMsg(err.message),
      },
    );
  };

  return (
    <form className="ready-card" onSubmit={onSubmit}>
      <div className="rs-h">
        <span className="rs-ico"><Icon.pkg /></span>
        <div className="rs-meta">
          <h4>Lista para envío</h4>
          <p>
            Indicá la fecha en que el pedido estará empacado y listo para
            que la transportadora lo recoja. Al confirmar, la card pasa a
            la columna <b>En preparación</b>.
          </p>
        </div>
      </div>

      <div className="rs-fields">
        <label className="rs-field">
          <span className="rs-lbl">Fecha</span>
          <input
            type="date"
            value={date}
            min={todayIso}
            onChange={(e) => setDate(e.target.value)}
            required
          />
        </label>
        <label className="rs-field">
          <span className="rs-lbl">Hora (opcional)</span>
          <input
            type="time"
            value={time}
            onChange={(e) => setTime(e.target.value)}
          />
        </label>
      </div>

      <label className="rs-field full">
        <span className="rs-lbl">Nota para empaque (opcional)</span>
        <input
          type="text"
          placeholder="Ej: cliente prefiere antes de las 10am"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>

      <div className="rs-foot">
        <span className="rs-hint">
          Sin agendar, la orden se queda en la columna "Nueva" y NO se
          puede avanzar a preparación.
        </span>
        <MacButton primary sm disabled={schedule.isPending}>
          {schedule.isPending ? "Agendando…" : "Confirmar"}
        </MacButton>
      </div>

      {errorMsg && (
        <div
          style={{
            marginTop: 8,
            padding: 8,
            background: "rgba(255,114,105,0.12)",
            border: "1px solid rgba(255,114,105,0.3)",
            color: "#ff7269",
            fontSize: 11,
            borderRadius: 4,
          }}
        >
          {errorMsg}
        </div>
      )}
    </form>
  );
}
