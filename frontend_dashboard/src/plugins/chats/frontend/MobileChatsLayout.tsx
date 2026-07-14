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
import { useCallback, useEffect, useRef, useState } from "react";

import { Icon } from "@/shared/ui";
import { useSelection } from "@/shared/lib";
import { useInvalidateOnReconnect } from "@/shared/api";
import { canLogout, logout } from "@/shared/config";
import { useQueryClient } from "@tanstack/react-query";

import {
  useChatInbox,
  useSessionsStream,
} from "@plugins/chats/frontend/entities/chat";
import { orderRefKeys } from "@plugins/chats/frontend/entities/order-ref";
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
      // PM2-M6: los pedidos del cliente no tienen push SSE propio — tras un
      // background largo (Android mata el socket) el sheet abierto quedaba
      // con botones stale si otro dispositivo cambió el estado.
      qc.invalidateQueries({ queryKey: orderRefKeys.all });
    }, [qc, selectedChatId]),
  );

  // PM2-M1: contador de entradas de historial NUESTRAS aún vivas. Los cierres
  // desde la UI hacen `history.back()` (el popstate es la ÚNICA fuente del
  // cambio de estado) — antes solo pusheaban al abrir y el cierre por UI
  // dejaba entradas huérfanas: tras N ciclos el back físico necesitaba N taps
  // muertos para salir, o saltaba vistas. El contador evita hacer back()
  // más allá de nuestras entradas (p.ej. tras un reload que resetea el estado
  // pero no el historial del WebView).
  const ownedHistoryEntries = useRef(0);

  const pushOwnedEntry = (tag: string) => {
    window.history.pushState({ mobileChats: tag }, "");
    ownedHistoryEntries.current += 1;
  };

  /** Cierra la vista superior (sheet, o el chat) consumiendo SU entrada. */
  const closeTop = useCallback(() => {
    if (ownedHistoryEntries.current > 0) {
      window.history.back(); // el popstate aplica el cambio de estado
      return;
    }
    // Fallback (reload perdió nuestro stack): cerrar directo, sin history.
    setActiveSheet((sheet) => {
      if (sheet !== "none") return "none";
      setView("inbox");
      return sheet;
    });
  }, []);

  const { data: chats = [] } = useChatInbox();
  const openChat = (id: string) => {
    setSelectedChatId(id);
    setView("chat");
    setActiveSheet("none");
    // PM-F4: entrada de historial para que el BACK FÍSICO de Android navegue
    // chat→inbox en vez de cerrar la app.
    pushOwnedEntry("chat");
  };
  // Notificación del sistema cuando una conversación pasa a manos del humano
  // (escalada del bot o intervene desde otro dispositivo) y la app no está en
  // primer plano. Tocar la notificación (web) abre ese chat (PM2-M7).
  useHandoffNotifications(chats, { onOpenChat: openChat });
  const selectedChat = chats.find((c) => c.id === selectedChatId) ?? null;

  const toggleSheet = (sheet: Exclude<ActiveSheet, "none">) => {
    if (activeSheet === sheet) {
      closeTop();
    } else if (activeSheet !== "none") {
      // Cambiar de sheet NO pushea otra entrada: "algún sheet abierto" es UNA
      // sola profundidad de navegación (orders↔inspector alternaban 2
      // entradas por ciclo).
      setActiveSheet(sheet);
    } else {
      setActiveSheet(sheet);
      pushOwnedEntry("sheet");
    }
  };

  // Back físico / gesto de Android (y los back() de closeTop) → cerrar primero
  // el sheet abierto, después el chat.
  useEffect(() => {
    const onPop = () => {
      ownedHistoryEntries.current = Math.max(0, ownedHistoryEntries.current - 1);
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
            onClick={closeTop}
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
            onClick={closeTop}
            role="presentation"
          >
            {/* PM2-M9: el dialog es el SHEET (con nombre accesible), no el
                backdrop clickeable — TalkBack anunciaba un dialog sin nombre
                cuyo tap-para-cerrar era indescubrible. */}
            <div
              className="mobile-sheet"
              role="dialog"
              aria-modal="true"
              aria-label={
                activeSheet === "orders"
                  ? "Pedidos del cliente"
                  : "Detalles del contacto"
              }
              onClick={(e) => e.stopPropagation()}
            >
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
