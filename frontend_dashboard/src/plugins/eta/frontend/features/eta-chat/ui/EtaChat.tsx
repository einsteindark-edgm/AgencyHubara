/**
 * Inspector derecho de ETA: chat en vivo entre el agente y el cliente.
 * Si `order` es null muestra un estado vacío.
 * Si el pedido necesita atención, banner rojo + "Intervenir manualmente".
 */

import type { TrackedOrder } from "@plugins/eta/frontend/entities/tracked-order";
import { Avatar, Icon } from "@/shared/ui";

interface Props {
  order: TrackedOrder | null;
}

export function EtaChat({ order }: Props) {
  if (!order) {
    return (
      <aside className="inspector eta-chat-wrap empty">
        <div className="ec-empty">
          <span className="ec-empty-i"><Icon.bot /></span>
          <div className="ec-empty-t">Sin pedido seleccionado</div>
          <div className="ec-empty-s">
            Toca un pedido en la lista para ver la conversación del agente con
            el cliente.
          </div>
        </div>
      </aside>
    );
  }

  // build chat history from events
  const messages: { kind: "bot" | "user"; time: string; body: string; flagged?: boolean }[] = [];
  order.events.forEach((ev) => {
    messages.push({ kind: "bot", time: ev.time, body: ev.agentMsg, flagged: ev.flagged });
    if (ev.reply) {
      messages.push({ kind: "user", time: ev.time, body: ev.reply, flagged: ev.flagged });
    }
  });

  return (
    <aside className="inspector eta-chat-wrap">
      <div className="ec-head">
        <Avatar initials={order.short} color={order.color} size={32} />
        <div className="ech-meta">
          <h3>{order.customer}</h3>
          <p>
            {order.channel} · {order.id}
          </p>
        </div>
        {order.needs && (
          <span className="ec-flag"><Icon.alert /></span>
        )}
      </div>

      {order.needs && (
        <div className="ec-alert">
          <Icon.alert />
          <div>
            <b>Chat necesita atención</b>
            <p>El cliente envió un mensaje que el agente no supo responder.</p>
          </div>
        </div>
      )}

      <div className="ec-body">
        {messages.map((m, i) => (
          <div
            key={i}
            className={
              "ec-bubble " +
              (m.kind === "bot" ? "bot" : "user") +
              (m.flagged && m.kind === "user" ? " flagged" : "")
            }
          >
            {m.kind === "bot" && (
              <div className="ec-bot-h">
                <span className="ec-bot-i"><Icon.bot /></span>
                <span>ETA agent</span>
              </div>
            )}
            <div className="ec-body-t">{m.body}</div>
            <div className="ec-time">{m.time}</div>
          </div>
        ))}

        {order.needs && (
          <div className="ec-bubble waiting">
            <span className="ec-w-dot" />
            <span className="ec-w-dot" />
            <span className="ec-w-dot" />
            <div className="ec-w-l">Agente esperando intervención</div>
          </div>
        )}
      </div>

      <div className="ec-foot">
        {order.needs ? (
          <button className="ec-take">
            <Icon.bot /> Intervenir manualmente
          </button>
        ) : (
          <div className="ec-pass">
            <Icon.bot />
            <span>El agente está respondiendo automáticamente</span>
          </div>
        )}
      </div>
    </aside>
  );
}
