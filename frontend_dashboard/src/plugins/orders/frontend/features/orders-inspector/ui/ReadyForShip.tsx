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
import { useScheduleOrder, type Order } from "@plugins/orders/frontend/entities/order";
import { Icon, MacButton } from "@/shared/ui";
import { addDaysIso, todayIso } from "@/shared/lib";

interface Props {
  order: Order;
}

export function ReadyForShip({ order }: Props) {
  // Default = mañana; el input no permite fechas pasadas (min = hoy).
  const minDate = todayIso();

  const [date, setDate] = useState<string>(order.dueIso || addDaysIso(1));
  const [time, setTime] = useState<string>(
    order.dueTime && order.dueTime !== "—" ? order.dueTime : "",
  );
  const [note, setNote] = useState<string>("");
  const schedule = useScheduleOrder();

  // F5.3 (auditoría 2026-06-10): el error NO se duplica en un useState — se
  // DERIVA de la mutation (única fuente del estado del flujo). Así no existen
  // combinaciones imposibles tipo "pending + error viejo en pantalla".
  const errorMsg = schedule.isPending
    ? null
    : schedule.isError
      ? schedule.error.message
      : schedule.data && !schedule.data.success
        ? (schedule.data.error_detail ?? "No se pudo agendar la entrega.")
        : null;

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    schedule.mutate({
      orderId: order.id,
      delivery_iso: date,
      delivery_time: time || undefined,
      note: note || undefined,
    });
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
            min={minDate}
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
