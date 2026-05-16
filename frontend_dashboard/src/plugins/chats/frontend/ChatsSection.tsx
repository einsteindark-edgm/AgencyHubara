/**
 * `ChatsSection` — la Page del plugin chats.
 *
 * Extraído de `pages/Dashboard.tsx` en PR2 (refactor a plugins). Mantiene la
 * misma firma de props que tenía cuando vivía inline en el shell, para que el
 * Dashboard pueda reemplazar `<ChatsSection ...>` con `<Page ...>` en PR3 sin
 * cambios funcionales.
 *
 * Auto-selecciona la primera sesión cuando llega data del SSE. La fuente del
 * stream sigue siendo el shell (`useSessionsStream()` en Dashboard.tsx) — el
 * plugin solo lee el cache de TanStack Query via `useChatInbox`.
 */
import { useEffect } from "react";

import { useChatInbox } from "@/entities/chat";

import { ChatsInbox } from "@plugins/chats/frontend/features/chats-inbox";
import { ChatsConversation } from "@plugins/chats/frontend/features/chats-conversation";
import { ChatsInspector } from "@plugins/chats/frontend/features/chats-inspector";

export interface ChatsSectionProps {
  showSidebar: boolean;
  showInspector: boolean;
  selectedChatId: string | null;
  setSelectedChatId: (id: string) => void;
}

export function ChatsSection({
  showSidebar,
  showInspector,
  selectedChatId,
  setSelectedChatId,
}: ChatsSectionProps) {
  const { data: chats = [] } = useChatInbox();
  useEffect(() => {
    if (selectedChatId == null && chats.length > 0) {
      setSelectedChatId(chats[0].id);
    }
  }, [chats, selectedChatId, setSelectedChatId]);

  return (
    <>
      {showSidebar && (
        <ChatsInbox
          selectedId={selectedChatId}
          onSelect={setSelectedChatId}
        />
      )}
      <ChatsConversation chatId={selectedChatId} />
      {showInspector && <ChatsInspector chatId={selectedChatId} />}
    </>
  );
}

export default ChatsSection;
