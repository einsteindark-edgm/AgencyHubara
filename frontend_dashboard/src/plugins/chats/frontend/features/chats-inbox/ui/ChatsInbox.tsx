/**
 * Sidebar de Chats: banner "Asignadas al humano" + pills de tag + calendario
 * de fechas + la lista agrupada en secciones.
 *
 * Las secciones ya vienen resueltas por `useInboxFilters` (Fijadas / Hoy /
 * Anteriores, o una sola de resultados cuando hay un rango elegido). Este
 * componente sólo las pinta: qué chat es "de hoy" es una decisión de dominio,
 * no de presentación — cuando vivía acá, "Hoy" terminó siendo `slice(0, 6)`.
 *
 * Recibe `selectedId / onSelect` por prop porque la selección es cross-feature
 * (la lee también `chats-conversation` y `chats-inspector`). El owner natural
 * es la página `Dashboard`.
 */

import {
  ORDER_BADGE_META,
  useChatInbox,
  type ChatInboxItem,
  type ChatOrderBadge,
  type ChatPostponedBadge,
} from "@plugins/chats/frontend/entities/chat";
import { Avatar, DateRangeFilter, Icon } from "@/shared/ui";
import { useInboxFilters } from "../model/useInboxFilters";
import { SoundSettings } from "./SoundSettings";

interface Props {
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function ChatsInbox({ selectedId, onSelect }: Props) {
  const { data: chats = [] } = useChatInbox();
  const f = useInboxFilters(chats);
  const isHuman = f.activeFilter === "Humano";
  // El banner cuenta sobre el rango de fechas y la búsqueda vigentes, igual
  // que los pills — si no, dice "7 esperando" y la lista muestra 2.
  const humanCount = f.filters.find((t) => t.key === "Humano")?.count ?? 0;
  const allCount = f.filters.find((t) => t.key === "Todas")?.count ?? 0;

  return (
    <aside className="sidebar">
      <div className="side-header">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 14, fontWeight: 700, letterSpacing: "-0.01em" }}>
            Bandeja
          </span>
        </div>

        <div className="side-search">
          <Icon.search />
          <input
            placeholder="Buscar conversaciones…"
            aria-label="Buscar conversaciones"
            value={f.query}
            onChange={(e) => f.setQuery(e.target.value)}
          />
        </div>

        <button
          className={"human-banner" + (isHuman ? " on" : "")}
          onClick={() => f.setActiveFilter("Humano")}
        >
          <span className="hb-icon"><Icon.user /></span>
          <span className="hb-body">
            <span className="hb-t">Asignadas al humano</span>
            <span className="hb-s">
              {humanCount} conversaci{humanCount !== 1 ? "ones" : "ón"} esperando respuesta
            </span>
          </span>
          <span className="hb-count">{humanCount}</span>
        </button>

        <div className="side-tabs">
          {f.filters
            .filter((tab) => tab.key !== "Humano")
            .map((tab) => (
              <button
                key={tab.key}
                className={"pill" + (f.activeFilter === tab.key ? " on" : "")}
                onClick={() => f.setActiveFilter(tab.key)}
              >
                {tab.key !== "Todas" && (
                  <span className="dot" style={{ color: tab.color }} />
                )}
                {tab.key} <span className="ct">{tab.count}</span>
              </button>
            ))}
        </div>

        <DateRangeFilter
          value={f.dateRange}
          onChange={f.setDateRange}
          onClear={f.clearDateRange}
          activeDays={f.activeDays}
          today={f.today}
          label={f.dateRangeLabel}
        />

        <SoundSettings />
      </div>

      <div className="side-list">
        {f.filtered.length === 0 ? (
          <EmptyState
            query={f.query.trim()}
            matchesElsewhere={allCount}
            onShowAll={() => f.setActiveFilter("Todas")}
            onClearQuery={f.clearQuery}
            hasDateRange={f.hasDateRange}
            isHuman={isHuman}
            onClear={f.clearDateRange}
          />
        ) : (
          f.sections.map((section) => (
            <div key={section.key}>
              <SectionHeader title={section.title} count={section.items.length} />
              {section.items.map((c) => (
                <Row key={c.id} chat={c} selected={selectedId === c.id} onSelect={onSelect} />
              ))}
            </div>
          ))
        )}
      </div>
    </aside>
  );
}

/** Una bandeja vacía por una búsqueda o un filtro de fecha NO es "todo bajo
 *  control": es un filtro que esconde conversaciones. Decirlo evita el susto
 *  de creer que se perdieron los chats, y ofrece la salida en el mismo lugar. */
function EmptyState({
  query,
  matchesElsewhere,
  onShowAll,
  onClearQuery,
  hasDateRange,
  isHuman,
  onClear,
}: {
  query: string;
  /** Coincidencias de la búsqueda en todas las vistas (el pill "Todas"). */
  matchesElsewhere: number;
  onShowAll: () => void;
  onClearQuery: () => void;
  hasDateRange: boolean;
  isHuman: boolean;
  onClear: () => void;
}) {
  // Lo buscado está en otro pill: la vista por defecto es la cola del humano
  // y el cliente que se busca casi nunca está ahí — un vacío mudo haría creer
  // que el buscador no encuentra.
  if (query && matchesElsewhere > 0) {
    return (
      <div className="empty-human">
        <span className="eh-ico"><Icon.search /></span>
        <div className="eh-t">Sin resultados en esta vista</div>
        <div className="eh-s">
          {matchesElsewhere} conversaci{matchesElsewhere !== 1 ? "ones" : "ón"} con «{query}» en otras vistas.
        </div>
        <button className="cal-preset" style={{ marginTop: 10 }} onClick={onShowAll}>
          Ver en Todas
        </button>
      </div>
    );
  }
  if (query) {
    return (
      <div className="empty-human">
        <span className="eh-ico"><Icon.search /></span>
        <div className="eh-t">Sin resultados para «{query}»</div>
        <div className="eh-s">Probá con otro teléfono, palabra o # de pedido.</div>
        <button className="cal-preset clear" style={{ marginTop: 10 }} onClick={onClearQuery}>
          Limpiar búsqueda
        </button>
      </div>
    );
  }
  if (hasDateRange) {
    return (
      <div className="empty-human">
        <span className="eh-ico"><Icon.cal /></span>
        <div className="eh-t">Sin conversaciones en esas fechas</div>
        <div className="eh-s">Probá con otro día o quitá el filtro.</div>
        <button className="cal-preset clear" style={{ marginTop: 10 }} onClick={onClear}>
          Quitar filtro de fecha
        </button>
      </div>
    );
  }
  if (isHuman) {
    return (
      <div className="empty-human">
        <span className="eh-ico"><Icon.check /></span>
        <div className="eh-t">Todo bajo control</div>
        <div className="eh-s">
          No hay conversaciones que requieran intervención humana.
        </div>
      </div>
    );
  }
  return (
    <div className="empty-human">
      <span className="eh-ico"><Icon.chat /></span>
      <div className="eh-t">Sin conversaciones</div>
      <div className="eh-s">No hay chats con este filtro.</div>
    </div>
  );
}

function SectionHeader({ title, count }: { title: string; count: number }) {
  return (
    <div className="side-section">
      <span className="caret"><Icon.caret /></span>
      {title}
      <span className="ct">{count}</span>
    </div>
  );
}

interface RowProps {
  chat: ChatInboxItem;
  selected: boolean;
  onSelect: (id: string) => void;
}

function Row({ chat, selected, onSelect }: RowProps) {
  // Pospuesto con la fecha ya pasada: la fila entera en rojo + aviso — el
  // humano tiene que retomar esta conversación (pedido del operador).
  const overdue = Boolean(chat.postponed?.overdue);
  return (
    <div
      className={"row" + (selected ? " sel" : "") + (overdue ? " row-overdue" : "")}
      onClick={() => onSelect(chat.id)}
    >
      <Avatar initials={chat.short} color={chat.color} presence={chat.presence} />
      <div className="row-body">
        <div className="name-row">
          <span className="name">{chat.name}</span>
          {overdue && (
            <span
              className="overdue-alert"
              role="img"
              aria-label="Vencido: hay que retomar esta conversación"
              title="Vencido: hay que retomar esta conversación"
            >
              <Icon.alert />
            </span>
          )}
          <span className="time">{chat.time}</span>
        </div>
        <div className="snippet">{chat.snippet}</div>
        <div className="meta-row">
          <span className={"tag " + chat.tagClass}>{chat.tag.replace(/_/g, " ")}</span>
          {chat.order && <OrderChip order={chat.order} />}
          {chat.postponed && <PostponedChip postponed={chat.postponed} />}
          {chat.unread > 0 && <span className="badge-unread">{chat.unread}</span>}
        </div>
      </div>
    </div>
  );
}

/**
 * A qué pedido pertenece la conversación. En el filtro "Asignadas al humano"
 * conviven chats que ya son una orden (falta verificar el pago, coordinar el
 * envío…) con chats que siguen negociando: sin esta marca hay que abrir cada
 * uno para saber cuál es cuál.
 *
 * El color es del PAGO, no del estado logístico (ese vive en Medusa y lo
 * muestra el panel de pedidos). El estado NO viaja sólo en el color: el
 * `aria-label` y el tooltip lo dicen con palabras.
 */
/**
 * Chip del cliente pospuesto: cuándo dijo que retoma y si la cita (el toque de
 * ese día) ya salió. Lo que escribió el cliente va en el texto accesible y el
 * tooltip — el operador lo ve sin abrir el chat.
 */
function PostponedChip({ postponed }: { postponed: ChatPostponedBadge }) {
  const said = postponed.text ? ` — «${postponed.text}»` : "";
  return (
    <span
      className={"postponed-chip postponed-chip-" + postponed.status}
      aria-label={`Pospuesto: ${postponed.description}${said}`}
      title={`Pospuesto: ${postponed.description}${said}`}
    >
      {postponed.label}
    </span>
  );
}

function OrderChip({ order }: { order: ChatOrderBadge }) {
  const meta = ORDER_BADGE_META[order.payment];
  // "#33 +1" — el pedido más reciente y cuántos más lleva el cliente.
  const text =
    order.count > 1 ? `${order.label} +${order.count - 1}` : order.label;
  return (
    <span
      className={"order-chip order-chip-" + meta.tone}
      aria-label={`Pedido ${order.label}, ${meta.label}`}
      title={`Pedido ${order.label} · ${meta.label}`}
    >
      <Icon.pkg />
      {text}
    </span>
  );
}
