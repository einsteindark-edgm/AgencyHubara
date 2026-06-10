/**
 * Sidebar de Chats: banner "Asignadas al humano" + pills de tag + secciones
 * (Fijadas / Hoy o Esperando respuesta / Anteriores).
 *
 * Recibe `selectedId / onSelect` por prop porque la selección es cross-feature
 * (la lee también `chats-conversation` y `chats-inspector`). El owner natural
 * es la página `Dashboard`.
 */

import { useChatInbox, type ChatInboxItem } from "@plugins/chats/frontend/entities/chat";
import { Avatar, Icon } from "@/shared/ui";
import { useInboxFilters } from "../model/useInboxFilters";

interface Props {
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function ChatsInbox({ selectedId, onSelect }: Props) {
  const { data: chats = [] } = useChatInbox();
  const f = useInboxFilters(chats);
  const humanCount = chats.filter((c) => c.human).length;
  const isHuman = f.activeFilter === "Humano";

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
      </div>

      <div className="side-list">
        {isHuman && f.filtered.length === 0 ? (
          <div className="empty-human">
            <span className="eh-ico"><Icon.check /></span>
            <div className="eh-t">Todo bajo control</div>
            <div className="eh-s">
              No hay conversaciones que requieran intervención humana.
            </div>
          </div>
        ) : (
          <>
            {f.pinned.length > 0 && (
              <>
                <SectionHeader title="Fijadas" count={f.pinned.length} />
                {f.pinned.map((c) => (
                  <Row key={c.id} chat={c} selected={selectedId === c.id} onSelect={onSelect} />
                ))}
              </>
            )}
            {f.today.length > 0 && (
              <>
                <SectionHeader
                  title={isHuman ? "Esperando respuesta" : "Hoy"}
                  count={f.today.length}
                />
                {f.today.map((c) => (
                  <Row key={c.id} chat={c} selected={selectedId === c.id} onSelect={onSelect} />
                ))}
              </>
            )}
            {f.earlier.length > 0 && (
              <>
                <SectionHeader title="Anteriores" count={f.earlier.length} />
                {f.earlier.map((c) => (
                  <Row key={c.id} chat={c} selected={selectedId === c.id} onSelect={onSelect} />
                ))}
              </>
            )}
          </>
        )}
      </div>
    </aside>
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
