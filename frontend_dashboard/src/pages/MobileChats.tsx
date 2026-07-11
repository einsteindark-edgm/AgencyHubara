/**
 * Shell móvil: la app Android es SOLO la sección de chats, sin el chrome de
 * escritorio (toolbar/statusbar/registry completo) — arranque y memoria
 * mínimos. Provee el `PluginHostProvider` (que el Page de chats necesita para
 * `useSelection`) y monta la Page a pantalla completa; la Page detecta
 * `IS_MOBILE_APP` y renderiza su layout de una columna.
 *
 * Deliberadamente NO reusa `Dashboard`: el operador en el teléfono quiere el
 * inbox al instante, no el shell macOS de 3 columnas. `main.tsx` elige entre
 * este shell y `Dashboard` según `IS_MOBILE_APP`.
 */
import { Suspense, lazy, useCallback, useMemo, useState } from "react";

import { ErrorBoundary } from "@/shared/ui";
import { PluginHostProvider } from "@/shared/lib";

// Lazy (dynamic import): mismo patrón que el registry generado del shell. Un
// import estático del plugin rompería el code-splitting y el gate R-REALTIME
// (el shell/pages no acopla eager a un plugin). En móvil, cargar solo el chunk
// de chats además baja el tiempo de arranque.
const ChatsSection = lazy(() => import("@plugins/chats/frontend"));

export function MobileChatsApp() {
  const [selection, setSelectionMap] = useState<Record<string, string | null>>(
    {},
  );
  const setSelection = useCallback((key: string, id: string | null) => {
    setSelectionMap((prev) => (prev[key] === id ? prev : { ...prev, [key]: id }));
  }, []);
  // En móvil el chrome del shell no aplica; los toggles quedan en true por si
  // alguna feature los consulta.
  const host = useMemo(
    () => ({ showSidebar: true, showInspector: true, selection, setSelection }),
    [selection, setSelection],
  );

  return (
    <div className="mobile-app">
      <ErrorBoundary scope="mobile-chats">
        <PluginHostProvider value={host}>
          <Suspense fallback={<div className="mobile-topbar" />}>
            <ChatsSection />
          </Suspense>
        </PluginHostProvider>
      </ErrorBoundary>
    </div>
  );
}

export default MobileChatsApp;
