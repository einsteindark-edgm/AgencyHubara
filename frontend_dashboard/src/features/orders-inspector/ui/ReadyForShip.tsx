/**
 * Tarjeta "Listo para envío": el humano define fecha+hora del pedido empacado.
 * Estado local — al confirmar muestra un check por 2 s y se resetea.
 */

import { useState } from "react";
import type { Order } from "@/entities/order";
import { Icon, MacButton } from "@/shared/ui";

interface Props {
  order: Order;
}

export function ReadyForShip({ order }: Props) {
  const [date, setDate] = useState(order.dueIso);
  const [time, setTime] = useState(order.dueTime);
  const [note, setNote] = useState("");
  const [saved, setSaved] = useState(false);

  const save = () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  return (
    <div className="ready-card">
      <div className="rs-h">
        <span className="rs-ico"><Icon.pkg /></span>
        <div className="rs-meta">
          <h4>Listo para envío</h4>
          <p>
            Indica cuándo el pedido estará empacado y listo para que la
            transportadora lo recoja.
          </p>
        </div>
        {saved && (
          <span className="rs-saved">
            <Icon.check /> Guardado
          </span>
        )}
      </div>

      <div className="rs-fields">
        <label className="rs-field">
          <span className="rs-lbl">Fecha</span>
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
          />
        </label>
        <label className="rs-field">
          <span className="rs-lbl">Hora</span>
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
          placeholder="Ej. Empaque para regalo con tarjeta crema"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>

      <div className="rs-foot">
        <span className="rs-hint">
          Se notificará a la transportadora automáticamente.
        </span>
        <MacButton primary sm onClick={save}>
          Confirmar
        </MacButton>
      </div>
    </div>
  );
}
