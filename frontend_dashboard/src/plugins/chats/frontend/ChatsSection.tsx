/**
 * `ChatsSection` — la Page del plugin chats.
 *
 * Extraído de `pages/Dashboard.tsx` en PR2 (refactor a plugins). Mantiene la
 * misma firma de props que tenía cuando vivía inline en el shell, para que el
 * Dashboard pueda reemplazar `<ChatsSection ...>` con `<Page ...>` en PR3 sin
 * cambios funcionales.
 *
 * Auto-selecciona la primera sesión cuando llega data del SSE. F4 (INV-1): el
 * stream SSE es DEL PLUGIN — `useSessionsStream()` se monta acá (antes vivía
 * en el shell, que importaba la entity de chats: acople shell→dominio). El
 * stream vive mientras la sección Chats esté montada; al volver a entrar,
 * `useChatInbox` refetchea y el stream reconecta.
 */
import { useEffect } from "react";

import { IS_MOBILE, usePluginHost, useSelection } from "@/shared/lib";

import {
  useChatInbox,
  useSessionsStream,
} from "@plugins/chats/frontend/entities/chat";

import { ChatsInbox } from "@plugins/chats/frontend/features/chats-inbox";
import { ChatsConversation } from "@plugins/chats/frontend/features/chats-conversation";
import { ChatsInspector } from "@plugins/chats/frontend/features/chats-inspector";
import { MobileChatsLayout } from "./MobileChatsLayout";

export function ChatsSection() {
  // En teléfono el layout de 3 columnas es inutilizable: se muestra UNA columna
  // a la vez (inbox → conversación) con navegación propia. El data-layer (SSE,
  // hooks, auth) es idéntico; solo cambia la composición visual.
  if (IS_MOBILE) return <MobileChatsLayout />;
  return <DesktopChatsLayout />;
}

function DesktopChatsLayout() {
  // F7: chrome + selección llegan por el PluginHost (contrato genérico).
  const { showSidebar, showInspector } = usePluginHost();
  const [selectedChatId, setSelectedChatId] = useSelection("chats");
  useSessionsStream();
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
