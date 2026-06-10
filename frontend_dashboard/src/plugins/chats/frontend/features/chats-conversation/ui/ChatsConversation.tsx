/**
 * Centro de Chats: header del chat + sub-tabs (Chat/Notas/Archivos) + cuerpo.
 * El composer es un sub-componente con estado local (intervenido sí/no).
 */

import { useState } from "react";
import { useChatInbox, useChatMessages } from "@plugins/chats/frontend/entities/chat";
import { useSession } from "@plugins/chats/frontend/entities/session";
import { Avatar, Icon } from "@/shared/ui";
import { ChatsNotes } from "./ChatsNotes";
import { ChatsFiles } from "./ChatsFiles";
import { ChatsMessageList } from "./ChatsMessageList";

type SubTab = "Chat" | "Notas" | "Archivos";

interface Props {
  chatId: string | null;
}

export function ChatsConversation({ chatId }: Props) {
  const { data: chats = [] } = useChatInbox();
  const { data: messages = [] } = useChatMessages(chatId);
  // Misma query (cache compartido) que el composer — header y composer nunca
  // discrepan sobre quién maneja la conversación.
  const { data: session } = useSession(chatId);
  const [subTab, setSubTab] = useState<SubTab>("Chat");

  const chat = chats.find((c) => c.id === chatId) ?? null;

  if (!chatId || !chat) {
    return (
      <main
        className="center"
        style={{
          alignItems: "center",
          justifyContent: "center",
          color: "var(--fg-faint)",
        }}
      >
        <p style={{ fontSize: 13 }}>Selecciona una conversación para verla</p>
      </main>
    );
  }

  return (
    <main className="center">
      <Header
        name={chat.name}
        short={chat.short}
        color={chat.color}
        route={session?.active_agent_route}
      />

      <div className="sub-tabs">
        {(["Chat", "Notas", "Archivos"] as SubTab[]).map((tab) => (
          <button
            key={tab}
            className={"sub-tab" + (subTab === tab ? " on" : "")}
            onClick={() => setSubTab(tab)}
          >
            {tabIcon(tab)}
            {tab}
            <span className="ct">{tabCount(tab, messages.length)}</span>
          </button>
        ))}
        <span className="right">
          <button className="tb-btn" title="Buscar en chat">
            <Icon.search />
          </button>
          <button className="tb-btn" title="Expandir">
            <Icon.expand />
          </button>
        </span>
      </div>

      {subTab === "Notas" && <ChatsNotes chatId={chatId} />}
      {subTab === "Archivos" && <ChatsFiles chatId={chatId} />}
      {subTab === "Chat" && (
        <ChatsMessageList messages={messages} chatId={chatId} key={chatId ?? ""} />
      )}
    </main>
  );
}

function tabIcon(tab: SubTab) {
  if (tab === "Chat") return <Icon.chat />;
  if (tab === "Notas") return <Icon.notes />;
  return <Icon.files />;
}

function tabCount(tab: SubTab, msgCount: number) {
  if (tab === "Chat") return msgCount;
  if (tab === "Notas") return 3;
  return 5;
}

interface HeaderProps {
  name: string;
  short: string;
  color: string;
  /** `active_agent_route` de la sesión: humano | ventas | remarketing. */
  route?: string;
}

function Header({ name, short, color, route }: HeaderProps) {
  return (
    <div className="chat-header">
      <Avatar initials={short} color={color} size={38} presence="online" />
      <div className="info">
        <span className="nm">{name}</span>
        <span className="sub">
          <span className="live">En línea</span>
          <span style={{ color: "var(--fg-faint)" }}>·</span>
          <RouteState route={route} />
          <span style={{ color: "var(--fg-faint)" }}>·</span>
          WhatsApp API
        </span>
      </div>
      <div className="actions">
        <button className="tb-btn" title="Más">
          <Icon.more />
        </button>
      </div>
    </div>
  );
}

/** Quién maneja la conversación, de un vistazo en el header. El humano
 *  intervenido resalta (acento + bold); el bot va en tono normal. */
function RouteState({ route }: { route?: string }) {
  if (route === "humano") {
    return (
      <span style={{ color: "var(--color-accent-fg, #b45309)", fontWeight: 600 }}>
        Intervenido · bot en pausa
      </span>
    );
  }
  return <span>{route === "remarketing" ? "Bot remarketing" : "Bot ventas"}</span>;
}
