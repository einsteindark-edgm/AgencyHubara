/**
 * Layout de chats para teléfono (una columna a la vez).
 *
 * El layout desktop apila inbox + conversación + inspector en 3 columnas fijas
 * — inutilizable a 375px. Acá mostramos UNA vista a la vez con navegación
 * propia: inbox (lista) → tap en un chat → conversación (con botón atrás) →
 * inspector como bottom-sheet opcional. Reusa EXACTAMENTE las mismas features
 * y el mismo data-layer (SSE, hooks, auth) que el desktop; solo cambia la
 * composición visual.
 *
 * La selección vive en el PluginHost (`useSelection("chats")`) igual que en
 * desktop; la vista activa (inbox/chat) es estado local — es navegación pura
 * de UI, no server-state (regla 3 de la política de estado).
 */
import { useCallback, useState } from "react";

import { Icon } from "@/shared/ui";
import { useSelection } from "@/shared/lib";
import { useInvalidateOnReconnect } from "@/shared/api";
import { useQueryClient } from "@tanstack/react-query";

import {
  useChatInbox,
  useSessionsStream,
} from "@plugins/chats/frontend/entities/chat";
import { sessionKeys } from "@plugins/chats/frontend/entities/session";

import { ChatsInbox } from "@plugins/chats/frontend/features/chats-inbox";
import { ChatsConversation } from "@plugins/chats/frontend/features/chats-conversation";
import { ChatsInspector } from "@plugins/chats/frontend/features/chats-inspector";

type MobileView = "inbox" | "chat";

export function MobileChatsLayout() {
  const [selectedChatId, setSelectedChatId] = useSelection("chats");
  const [view, setView] = useState<MobileView>("inbox");
  const [showInspector, setShowInspector] = useState(false);
  const qc = useQueryClient();

  // El stream SSE es del plugin (INV-1). Al volver del background Android suele
  // matar el socket: cuando el stream reconecta, refrescamos las queries del
  // chat para no quedar con data vieja.
  useSessionsStream();
  useInvalidateOnReconnect(
    useCallback(() => {
      qc.invalidateQueries({ queryKey: sessionKeys.list() });
      if (selectedChatId) {
        qc.invalidateQueries({ queryKey: sessionKeys.detail(selectedChatId) });
      }
    }, [qc, selectedChatId]),
  );

  const { data: chats = [] } = useChatInbox();
  const selectedChat = chats.find((c) => c.id === selectedChatId) ?? null;

  const openChat = (id: string) => {
    setSelectedChatId(id);
    setView("chat");
  };
  const backToInbox = () => {
    setView("inbox");
    setShowInspector(false);
  };

  if (view === "chat" && selectedChatId) {
    return (
      <div className="is-mobile mobile-chats">
        <header className="mobile-topbar">
          <button
            className="mobile-back"
            onClick={backToInbox}
            aria-label="Volver a la bandeja"
          >
            <Icon.back />
          </button>
          <div className="mobile-topbar-title">
            {selectedChat?.name ?? "Conversación"}
          </div>
          <button
            className="mobile-inspector-toggle"
            onClick={() => setShowInspector((v) => !v)}
            aria-label="Detalles del contacto"
          >
            <Icon.info />
          </button>
        </header>
        <div className="mobile-conversation">
          <ChatsConversation chatId={selectedChatId} />
        </div>
        {showInspector && (
          <div
            className="mobile-sheet-backdrop"
            onClick={() => setShowInspector(false)}
            role="dialog"
            aria-modal="true"
          >
            <div
              className="mobile-sheet"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="mobile-sheet-handle" />
              <ChatsInspector chatId={selectedChatId} />
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="is-mobile mobile-chats">
      <header className="mobile-topbar">
        <div className="mobile-topbar-title">Chats</div>
      </header>
      <div className="mobile-inbox">
        <ChatsInbox selectedId={selectedChatId} onSelect={openChat} />
      </div>
    </div>
  );
}

export default MobileChatsLayout;
