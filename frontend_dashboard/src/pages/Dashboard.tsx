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
 * Modo híbrido durante migración:
 *   - `chat` se carga desde `PLUGINS` registry (plugin chats — PR2/PR3).
 *   - `agent` idem (plugin agents_admin — PR4).
 *   - `upload` idem (plugin catalog — PR5).
 *   - `eta` idem (plugin eta — PR6).
 *   - `orders` sigue inline hasta su PR (PR7).
 */

import { Suspense, useMemo, useState } from "react";

import { StatusBar, TitleBar, Toolbar, type SectionKey } from "@/shared/ui";
import { IS_DESKTOP } from "@/shared/lib";

import { useSessionsStream } from "@/entities/chat";
import { useOrders } from "@/entities/order";

// PR3: consumo dinámico del registry. Si el plugin no está en
// `ENABLED_PLUGINS`, `Page` es undefined y la sección queda vacía.
import { PLUGINS } from "@/app/plugin-registry.generated";

import {
  OrdersFilters,
  filterLabel,
  useOrderFilters,
} from "@/features/orders-filters";
import { OrdersBoard, OrdersHeader } from "@/features/orders-board";
import { OrdersInspector } from "@/features/orders-inspector";

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

          {section === "orders" && (
            <OrdersSection
              showSidebar={showSidebar}
              showInspector={showInspector}
              selectedOrderId={selectedOrderId}
              setSelectedOrderId={setSelectedOrderId}
            />
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

/* ── Section orchestrators ───────────────────────────────────────────── */

// Plugin sections (cargadas dinámicamente del registry):
//   - ChatsSection  → @plugins/chats/frontend          (PR2)
//   - AgentsSection → @plugins/agents_admin/frontend   (PR4)
//   - UploadSection → @plugins/catalog/frontend        (PR5)
//   - EtaSection    → @plugins/eta/frontend            (PR6)
//
// Inline secciones (pendientes de migración a plugin):
//   - OrdersSection (PR7).

interface OrdersSectionProps {
  showSidebar: boolean;
  showInspector: boolean;
  selectedOrderId: string | null;
  setSelectedOrderId: (id: string) => void;
}

function OrdersSection({
  showSidebar,
  showInspector,
  selectedOrderId,
  setSelectedOrderId,
}: OrdersSectionProps) {
  const { data: orders = [] } = useOrders();
  const f = useOrderFilters(orders);
  const filteredTotal = f.filtered.reduce((a, b) => a + b.total, 0);
  const selected = orders.find((o) => o.id === selectedOrderId) ?? null;

  return (
    <>
      {showSidebar && (
        <OrdersFilters
          view={f.view}
          setView={f.setView}
          payType={f.payType}
          setPayType={f.setPayType}
          orders={orders}
        />
      )}
      <main className="ord-canvas">
        <OrdersHeader
          orders={orders}
          filteredCount={f.filtered.length}
          filteredTotal={filteredTotal}
          title={filterLabel(f.view)}
        />
        <div className="ord-body">
          <OrdersBoard
            orders={f.filtered}
            selectedId={selectedOrderId}
            onSelect={setSelectedOrderId}
          />
        </div>
      </main>
      {showInspector && <OrdersInspector order={selected} />}
    </>
  );
}

