/**
 * Plugin `chats` — barrel del frontend.
 *
 * Exporta:
 *   - `default` (= ChatsSection): el componente "Page" que el shell renderiza
 *     cuando `section === "chat"`. Lo carga el registry generado via
 *     `lazy(() => import("@plugins/chats/frontend").then(assertPluginModule))`.
 *     Post-F7 NO recibe props: toma chrome y selección del PluginHost
 *     (`usePluginHost()` + `useSelection("chats")` — ver shared/lib/plugin-host).
 *   - Named re-exports de las 3 features superficiales (ChatsInbox,
 *     ChatsConversation, ChatsInspector) para tests y superficie pública estable.
 *
 * Las contributions de UI (sidebar, sections, dashboard_widgets) viven en
 * `plugin.yaml` (la única fuente de verdad consumida por
 * `scripts/plugins-sync.ts`).
 */
export { default, ChatsSection } from "./ChatsSection";

// Named re-exports — Dashboard.tsx (PR2) los importa directo de aquí en vez de
// de las features individuales para mantener una superficie pública estable.
export { ChatsInbox } from "@plugins/chats/frontend/features/chats-inbox";
export { ChatsConversation } from "@plugins/chats/frontend/features/chats-conversation";
export { ChatsInspector } from "@plugins/chats/frontend/features/chats-inspector";
