/**
 * Página `Dashboard`: shell macOS-style. Responsabilidades de esta capa:
 *
 *   - State de coordinación cross-feature (sección activa, selecciones por
 *     sección, toggles de sidebar/inspector).
 *   - Composición JSX de las features inline + carga de los plugins desde el
 *     registry auto-generado (PR3).
 *
 * NO va acá:
 *   - Data fetching de dominio (vive en entities/<x>/api.ts).
 *   - UI específica de una feature (vive en features/<x>/ui/).
 *
 * Post-PR7: TODAS las secciones se cargan dinámicamente del `PLUGINS`
 * registry. El shell ya no importa código de feature directamente; solo
 * consume el barrel del registry y delega el render a cada plugin.Page.
 */

import { Suspense, useMemo, useState } from "react";

import { StatusBar, TitleBar, Toolbar, type SectionKey } from "@/shared/ui";
import { IS_DESKTOP } from "@/shared/lib";

import { useSessionsStream } from "@/entities/chat";

// Post-PR7: consumo dinámico del registry para TODAS las secciones. Si un
// plugin no está en `ENABLED_PLUGINS`, su `Page` es undefined y la sección
// queda vacía (degradación graceful).
import { PLUGINS } from "@/app/plugin-registry.generated";

export function Dashboard() {
  const [section, setSection] = useState<SectionKey>("chat");
  const [showSidebar, setShowSidebar] = useState(true);
  const [showInspector, setShowInspector] = useState(true);

  // Selecciones cross-feature por sección. El chat real se elige cuando llegan
  // las sesiones del backend (no podemos prefijar un id mock que no existe).
  const [selectedChatId, setSelectedChatId] = useState<string | null>(null);
  const [selectedOrderId, setSelectedOrderId] = useState<string | null>("#1247");
  const [selectedTrackedId, setSelectedTrackedId] = useState<string | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<string>("j-now");
  const [selectedAgentId, setSelectedAgentId] = useState<string>("a3");

  // SSE de sesiones: se monta UNA vez aquí — empuja el snapshot al cache de
  // TanStack Query para todas las features de Chats. Las demás secciones del
  // diseño viven con datos mock locales y no dependen del stream.
  useSessionsStream();

  // PR3+: lookup de plugins por id. Memoizado para evitar re-find en cada render.
  // Si el plugin no está habilitado (ENABLED_PLUGINS), `Page` es undefined y la
  // sección no renderiza nada (degradación graceful).
  const ChatsPage = useMemo(
    () => PLUGINS.find((p) => p.id === "chats")?.Page,
    [],
  );
  const AgentsPage = useMemo(
    () => PLUGINS.find((p) => p.id === "agents_admin")?.Page,
    [],
  );
  const UploadPage = useMemo(
    () => PLUGINS.find((p) => p.id === "catalog")?.Page,
    [],
  );
  const EtaPage = useMemo(
    () => PLUGINS.find((p) => p.id === "eta")?.Page,
    [],
  );
  const OrdersPage = useMemo(
    () => PLUGINS.find((p) => p.id === "orders")?.Page,
    [],
  );

  return (
    <div className={"stage" + (IS_DESKTOP ? "" : " is-web")}>
      <div className="win">
        {IS_DESKTOP && <TitleBar />}
        <Toolbar
          section={section}
          setSection={setSection}
          showSidebar={showSidebar}
          setShowSidebar={setShowSidebar}
          showInspector={showInspector}
          setShowInspector={setShowInspector}
        />

        <div className="body">
          {section === "chat" && ChatsPage && (
            <Suspense fallback={null}>
              <ChatsPage
                showSidebar={showSidebar}
                showInspector={showInspector}
                selectedChatId={selectedChatId}
                setSelectedChatId={setSelectedChatId}
              />
            </Suspense>
          )}

          {section === "orders" && OrdersPage && (
            <Suspense fallback={null}>
              <OrdersPage
                showSidebar={showSidebar}
                showInspector={showInspector}
                selectedOrderId={selectedOrderId}
                setSelectedOrderId={setSelectedOrderId}
              />
            </Suspense>
          )}

          {section === "eta" && EtaPage && (
            <Suspense fallback={null}>
              <EtaPage
                showSidebar={showSidebar}
                showInspector={showInspector}
                selectedTrackedId={selectedTrackedId}
                setSelectedTrackedId={setSelectedTrackedId}
              />
            </Suspense>
          )}

          {section === "upload" && UploadPage && (
            <Suspense fallback={null}>
              <UploadPage
                showSidebar={showSidebar}
                showInspector={showInspector}
                selectedJobId={selectedJobId}
                setSelectedJobId={setSelectedJobId}
              />
            </Suspense>
          )}

          {section === "agent" && AgentsPage && (
            <Suspense fallback={null}>
              <AgentsPage
                showSidebar={showSidebar}
                showInspector={showInspector}
                selectedAgentId={selectedAgentId}
                setSelectedAgentId={setSelectedAgentId}
              />
            </Suspense>
          )}
        </div>

        <StatusBar />
      </div>
    </div>
  );
}

/* Post-PR7: el shell ya no contiene componentes de sección. Cada plugin
 * exporta su `Page` desde su barrel; el registry generado los expone como
 * `lazy()` components que el shell renderiza con `<Suspense>`.
 *
 * Plugins migrados (orden de PR):
 *   - ChatsSection  → @plugins/chats/frontend          (PR2)
 *   - AgentsSection → @plugins/agents_admin/frontend   (PR4)
 *   - UploadSection → @plugins/catalog/frontend        (PR5)
 *   - EtaSection    → @plugins/eta/frontend            (PR6)
 *   - OrdersSection → @plugins/orders/frontend         (PR7)
 */

