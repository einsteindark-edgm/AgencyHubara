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

import { useChatInbox, type ChatInboxItem } from "@plugins/chats/frontend/entities/chat";
import { Avatar, Icon } from "@/shared/ui";
import { useInboxFilters } from "../model/useInboxFilters";
import { InboxDateFilter } from "./InboxDateFilter";

interface Props {
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function ChatsInbox({ selectedId, onSelect }: Props) {
  const { data: chats = [] } = useChatInbox();
  const f = useInboxFilters(chats);
  const isHuman = f.activeFilter === "Humano";
  // El banner cuenta sobre el rango de fechas vigente, igual que los pills —
  // si no, dice "7 esperando" y la lista muestra 2.
  const humanCount = f.filters.find((t) => t.key === "Humano")?.count ?? 0;

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
          <input placeholder="Buscar conversaciones…" />
        </div>

        <button
          className={"human-banner" + (isHuman ? " on" : "")}
          onClick={() => f.setActiveFilter("Humano")}
        >
          <span className="hb-icon"><Icon.user /></span>
          <span className="hb-body">
            <span className="hb-t">Asignadas al humano</span>
            <span className="hb-s">
              {humanCount} conversación{humanCount !== 1 ? "es" : ""} esperando respuesta
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

        <InboxDateFilter
          value={f.dateRange}
          onChange={f.setDateRange}
          onClear={f.clearDateRange}
          activeDays={f.activeDays}
          today={f.today}
          label={f.dateRangeLabel}
        />
      </div>

      <div className="side-list">
        {f.filtered.length === 0 ? (
          <EmptyState hasDateRange={f.hasDateRange} isHuman={isHuman} onClear={f.clearDateRange} />
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

/** Una bandeja vacía por un filtro de fecha NO es "todo bajo control": es un
 *  filtro que esconde conversaciones. Decirlo evita el susto de creer que se
 *  perdieron los chats, y ofrece la salida en el mismo lugar. */
function EmptyState({
  hasDateRange,
  isHuman,
  onClear,
}: {
  hasDateRange: boolean;
  isHuman: boolean;
  onClear: () => void;
}) {
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
  return (
    <div
      className={"row" + (selected ? " sel" : "")}
      onClick={() => onSelect(chat.id)}
    >
      <Avatar initials={chat.short} color={chat.color} presence={chat.presence} />
      <div className="row-body">
        <div className="name-row">
          <span className="name">{chat.name}</span>
          <span className="time">{chat.time}</span>
        </div>
        <div className="snippet">{chat.snippet}</div>
        <div className="meta-row">
          <span className={"tag " + chat.tagClass}>{chat.tag}</span>
          {chat.unread > 0 && <span className="badge-unread">{chat.unread}</span>}
        </div>
      </div>
    </div>
  );
}
