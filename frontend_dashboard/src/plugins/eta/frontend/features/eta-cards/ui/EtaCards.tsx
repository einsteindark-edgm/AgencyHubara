/**
 * Pantalla central de ETA: pedidos rastreados AGRUPADOS POR CLIENTE.
 *
 * Cada grupo lleva un header sticky con la identidad del cliente (avatar,
 * nombre, teléfono), cuántos pedidos tiene en seguimiento, si alguno necesita
 * atención y cuánto hay que cobrarle hoy (COD en la calle). Las tarjetas
 * dentro del grupo son compactas: el header ya carga la identidad, así que la
 * tarjeta abre con el número de pedido y su mini-stepper. Diseñado para
 * escalar a muchos seguimientos sin repetir cliente en cada tarjeta.
 */

import { useMemo } from "react";

import {
  TRACKED_STAGES,
  type TrackedOrder,
} from "@plugins/eta/frontend/entities/tracked-order";
import { truncate } from "@/shared/lib";
import { Avatar, Icon } from "@/shared/ui";

import { groupTrackedOrders, type TrackedOrderGroup } from "../model/groupTrackedOrders";

interface Props {
  orders: TrackedOrder[];
  filterLabel: string;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

/** "573125671604" → "+57 312 567 1604" (best-effort; fallback `+<raw>`). */
function formatPhone(phone: string): string {
  if (/^57\d{10}$/.test(phone)) {
    return `+57 ${phone.slice(2, 5)} ${phone.slice(5, 8)} ${phone.slice(8)}`;
  }
  return `+${phone}`;
}

export function EtaCards({ orders, filterLabel, selectedId, onSelect }: Props) {
  const groups = useMemo(() => groupTrackedOrders(orders), [orders]);

  return (
    <main className="eta-canvas">
      <div className="eta-list-head">
        <div>
          <h1>{filterLabel}</h1>
          <p>
            {orders.length} pedido{orders.length !== 1 ? "s" : ""} ·{" "}
            {groups.length} cliente{groups.length !== 1 ? "s" : ""} · Toca un
            pedido para ver la conversación
          </p>
        </div>
      </div>

      <div className="eta-list-body">
        {groups.map((g) => (
          <Group
            key={g.key}
            group={g}
            selectedId={selectedId}
            onSelect={onSelect}
          />
        ))}
        {orders.length === 0 && (
          <div className="eta-empty">
            <span className="ee-ico"><Icon.check /></span>
            <div className="ee-t">Nada que mostrar aquí</div>
            <div className="ee-s">
              No hay pedidos que coincidan con el filtro actual.
            </div>
          </div>
        )}
      </div>
    </main>
  );
}

interface GroupProps {
  group: TrackedOrderGroup;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

function Group({ group, selectedId, onSelect }: GroupProps) {
  return (
    <section className="eta-group">
      <div className="eta-group-h">
        <Avatar initials={group.short} color={group.color} size={26} />
        <div className="eg-id">
          <span className="eg-name">{group.customer}</span>
          {group.phone && (
            <span className="eg-phone">{formatPhone(group.phone)}</span>
          )}
        </div>
        {group.needs && (
          <span className="eg-flag" title="Algún pedido necesita atención">
            <Icon.alert />
          </span>
        )}
        {group.codPending > 0 && (
          <span
            className="eg-cod"
            title="Contra entrega en la calle — se cobra al entregar"
          >
            A cobrar hoy <b>$ {group.codPending.toLocaleString("es-CO")}</b>
          </span>
        )}
        <span className="eg-count">
          {group.orders.length} pedido{group.orders.length !== 1 ? "s" : ""}
        </span>
      </div>
      <div className="eta-group-cards">
        {group.orders.map((o) => (
          <Card
            key={o.id}
            order={o}
            selected={selectedId === o.id}
            onSelect={onSelect}
          />
        ))}
      </div>
    </section>
  );
}

interface CardProps {
  order: TrackedOrder;
  selected: boolean;
  onSelect: (id: string) => void;
}

function Card({ order, selected, onSelect }: CardProps) {
  const idx = TRACKED_STAGES.findIndex((s) => s.key === order.current);
  const lastEvent = order.events[order.events.length - 1];
  const lastReplyEvent = [...order.events].reverse().find((e) => e.reply);

  return (
    <div
      className={
        "eta-card" +
        (selected ? " sel" : "") +
        (order.needs ? " flagged" : "")
      }
      onClick={() => onSelect(order.id)}
    >
      <div className="ec-row1">
        <div className="ec-id-block">
          <div className="ec-id-name">
            <span className="ec-id strong">{order.id}</span>
          </div>
          <div className="ec-meta-row">
            {order.city && (
              <>
                <span className="ec-city">
                  <Icon.loc />
                  {order.city}
                </span>
                <span className="dot-sep">·</span>
              </>
            )}
            <span>{order.channel}</span>
          </div>
        </div>
        <div className="ec-tags">
          {order.payType === "cod" && (
            <span
              className="pay-badge cod"
              title={"A cobrar: $ " + order.total.toLocaleString("es-CO")}
            >
              <Icon.truck /> Contra entrega
            </span>
          )}
          {order.payType === "confirmed" && (
            // "Anticipado" = modalidad (no cash on delivery). NO es estado
            // de pago — el estado real (paid/pending/etc.) vive en orders.
            <span className="pay-badge confirmed">
              <Icon.check /> Anticipado
            </span>
          )}
          {order.needs && (
            <span className="pay-badge attention">
              <Icon.alert /> Atención
            </span>
          )}
        </div>
      </div>

      <div className="ec-mini-stepper">
        {TRACKED_STAGES.map((s, i) => {
          const isPast = i < idx;
          const isCurrent = i === idx;
          return (
            <div
              key={s.key}
              style={{
                display: "contents",
              }}
            >
              <div
                className={
                  "ms-step" +
                  (isPast ? " done" : "") +
                  (isCurrent ? " cur" : "")
                }
                style={
                  isPast || isCurrent
                    ? ({ "--sc": s.color } as React.CSSProperties)
                    : undefined
                }
              >
                <div className="ms-dot">
                  {isPast ? <Icon.check /> : isCurrent ? <span className="ms-pulse" /> : null}
                </div>
                <div className="ms-lbl">{s.label}</div>
              </div>
              {i < TRACKED_STAGES.length - 1 && (
                <div className={"ms-bar" + (i < idx ? " done" : "")} />
              )}
            </div>
          );
        })}
      </div>

      <div className="ec-last">
        {lastEvent ? (
          <>
            <div className="ec-last-row">
              <span className="ec-last-ico"><Icon.bot /></span>
              <span className="ec-last-t">Último mensaje del agente</span>
              <span className="ec-last-time">
                {lastEvent.date} · {lastEvent.time}
              </span>
            </div>
            <div className="ec-last-msg">{truncate(lastEvent.agentMsg, 130)}</div>
            {order.needs && lastReplyEvent && (
              <div className="ec-last-row reply">
                <span className="ec-last-ico flag"><Icon.alert /></span>
                <span className="ec-last-t">
                  Cliente respondió sin que el agente supiera contestar
                </span>
              </div>
            )}
          </>
        ) : (
          // El pedido está en seguimiento pero el agente todavía no envió
          // ninguna notificación (recién entró a fulfillment / aún sin cambio
          // de estado notificado). Sin esto, `lastEvent` undefined rompía la
          // tarjeta — los pedidos reales arrancan con timeline vacío.
          <div className="ec-last-row">
            <span className="ec-last-ico"><Icon.bot /></span>
            <span className="ec-last-t muted">
              Aún sin notificaciones · el agente avisará en el próximo cambio de estado
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
