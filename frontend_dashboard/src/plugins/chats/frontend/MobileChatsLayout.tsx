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
import { useCallback, useEffect, useState } from "react";

import { Icon } from "@/shared/ui";
import { useSelection } from "@/shared/lib";
import { useInvalidateOnReconnect } from "@/shared/api";
import { canLogout, logout } from "@/shared/config";
import { useQueryClient } from "@tanstack/react-query";

import {
  useChatInbox,
  useSessionsStream,
} from "@plugins/chats/frontend/entities/chat";
import { sessionKeys } from "@plugins/chats/frontend/entities/session";

import {
  ChatsInbox,
  useHandoffNotifications,
} from "@plugins/chats/frontend/features/chats-inbox";
import { ChatsConversation } from "@plugins/chats/frontend/features/chats-conversation";
import { ChatsInspector } from "@plugins/chats/frontend/features/chats-inspector";
import { ChatsOrdersPanel } from "@plugins/chats/frontend/features/chats-orders";

type MobileView = "inbox" | "chat";
/** Un solo bottom-sheet a la vez (inspector o pedidos). */
type ActiveSheet = "none" | "inspector" | "orders";

export function MobileChatsLayout() {
  const [selectedChatId, setSelectedChatId] = useSelection("chats");
  const [view, setView] = useState<MobileView>("inbox");
  const [activeSheet, setActiveSheet] = useState<ActiveSheet>("none");
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
  // Notificación del sistema cuando una conversación pasa a manos del humano
  // (escalada del bot o intervene desde otro dispositivo) y la app no está
  // en primer plano.
  useHandoffNotifications(chats);
  const selectedChat = chats.find((c) => c.id === selectedChatId) ?? null;

  const openChat = (id: string) => {
    setSelectedChatId(id);
    setView("chat");
    // PM-F4: empujar una entrada de historial para que el BACK FÍSICO de
    // Android navegue chat→inbox en vez de cerrar la app (el WebView mapea el
    // back del sistema a history.back()).
    window.history.pushState({ mobileChats: "chat" }, "");
  };
  const backToInbox = () => {
    setView("inbox");
    setActiveSheet("none");
  };
  const openSheet = (sheet: Exclude<ActiveSheet, "none">) => {
    setActiveSheet(sheet);
    window.history.pushState({ mobileChats: "sheet" }, "");
  };
  const toggleSheet = (sheet: Exclude<ActiveSheet, "none">) => {
    if (activeSheet === sheet) setActiveSheet("none");
    else openSheet(sheet);
  };

  // Back físico / gesto de Android → cerrar primero el sheet abierto, después
  // el chat. Idempotente: si el botón de la UI ya cerró el sheet, el pop no-op.
  useEffect(() => {
    const onPop = () => {
      setActiveSheet((sheet) => {
        if (sheet !== "none") return "none";
        setView("inbox");
        return sheet;
      });
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

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
            className="mobile-topbar-btn"
            onClick={() => toggleSheet("orders")}
            aria-label="Pedidos del cliente"
            title="Pedidos"
          >
            <Icon.box />
          </button>
          <button
            className="mobile-topbar-btn"
            onClick={() => toggleSheet("inspector")}
            aria-label="Detalles del contacto"
          >
            <Icon.info />
          </button>
        </header>
        <div className="mobile-conversation">
          <ChatsConversation chatId={selectedChatId} />
        </div>
        {activeSheet !== "none" && (
          <div
            className="mobile-sheet-backdrop"
            onClick={() => setActiveSheet("none")}
            role="dialog"
            aria-modal="true"
          >
            <div className="mobile-sheet" onClick={(e) => e.stopPropagation()}>
              <div className="mobile-sheet-handle" />
              {activeSheet === "inspector" && (
                <ChatsInspector chatId={selectedChatId} />
              )}
              {activeSheet === "orders" && (
                <ChatsOrdersPanel sessionId={selectedChatId} />
              )}
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
        {canLogout() && (
          <button
            className="mobile-topbar-btn"
            onClick={logout}
            aria-label="Cerrar sesión"
            title="Cerrar sesión"
          >
            <Icon.arrow />
          </button>
        )}
      </header>
      <div className="mobile-inbox">
        <ChatsInbox selectedId={selectedChatId} onSelect={openChat} />
      </div>
    </div>
  );
}

export default MobileChatsLayout;
