/**
 * Página `Dashboard`: shell macOS-style con cinco secciones (Chats / Órdenes /
 * Productos / ETA agent / Agentes). Responsabilidades de esta capa:
 *
 *   - State de coordinación cross-feature (sección activa, selecciones por
 *     sección, toggles de sidebar/inspector).
 *   - Composición JSX de las features.
 *
 * NO va acá:
 *   - Data fetching de dominio (vive en entities/<x>/api.ts).
 *   - UI específica de una feature (vive en features/<x>/ui/).
 */

import { useMemo, useState } from "react";

import { StatusBar, TitleBar, Toolbar, type SectionKey } from "@/shared/ui";
import { IS_DESKTOP } from "@/shared/lib";

import { useSessionsStream } from "@/entities/chat";
import { useOrders } from "@/entities/order";
import { useTrackedOrders } from "@/entities/tracked-order";

// PR2: la sección Chats vive en su plugin. El shell sigue siendo el dueño del
// stream SSE y de las selecciones cross-section; PR3 reemplazará este import
// estático por consumo dinámico del registry generado.
import { ChatsSection } from "@plugins/chats/frontend";

import {
  OrdersFilters,
  filterLabel,
  useOrderFilters,
} from "@/features/orders-filters";
import { OrdersBoard, OrdersHeader } from "@/features/orders-board";
import { OrdersInspector } from "@/features/orders-inspector";

import {
  EtaList,
  FILTER_LABELS as ETA_LABELS,
  useEtaFilters,
} from "@/features/eta-list";
import { EtaCards } from "@/features/eta-cards";
import { EtaChat } from "@/features/eta-chat";

import { UploadJobs } from "@/features/upload-jobs";
import { UploadWizard } from "@/features/upload-wizard";
import { UploadInspector } from "@/features/upload-inspector";

import { AgentsList } from "@/features/agents-list";
import { AgentsPrompts } from "@/features/agents-prompts";
import { AgentsInspector } from "@/features/agents-inspector";

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
          {section === "chat" && (
            <ChatsSection
              showSidebar={showSidebar}
              showInspector={showInspector}
              selectedChatId={selectedChatId}
              setSelectedChatId={setSelectedChatId}
            />
          )}

          {section === "orders" && (
            <OrdersSection
              showSidebar={showSidebar}
              showInspector={showInspector}
              selectedOrderId={selectedOrderId}
              setSelectedOrderId={setSelectedOrderId}
            />
          )}

          {section === "eta" && (
            <EtaSection
              showSidebar={showSidebar}
              showInspector={showInspector}
              selectedTrackedId={selectedTrackedId}
              setSelectedTrackedId={setSelectedTrackedId}
            />
          )}

          {section === "upload" && (
            <UploadSection
              showSidebar={showSidebar}
              showInspector={showInspector}
              selectedJobId={selectedJobId}
              setSelectedJobId={setSelectedJobId}
            />
          )}

          {section === "agent" && (
            <AgentsSection
              showSidebar={showSidebar}
              showInspector={showInspector}
              selectedAgentId={selectedAgentId}
              setSelectedAgentId={setSelectedAgentId}
            />
          )}
        </div>

        <StatusBar />
      </div>
    </div>
  );
}

/* ── Section orchestrators ───────────────────────────────────────────── */

// PR2: ChatsSection se exportó al plugin (`@plugins/chats/frontend`). Las
// otras secciones siguen inline hasta sus respectivos PRs (PR4 agents-admin,
// PR5 catalog, PR6 eta, PR7 orders).

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

interface EtaSectionProps {
  showSidebar: boolean;
  showInspector: boolean;
  selectedTrackedId: string | null;
  setSelectedTrackedId: (id: string) => void;
}

function EtaSection({
  showSidebar,
  showInspector,
  selectedTrackedId,
  setSelectedTrackedId,
}: EtaSectionProps) {
  const { data: tracked = [] } = useTrackedOrders();
  const f = useEtaFilters(tracked);
  const selected = useMemo(
    () => tracked.find((o) => o.id === selectedTrackedId) ?? null,
    [tracked, selectedTrackedId],
  );

  return (
    <>
      {showSidebar && (
        <EtaList orders={tracked} filter={f.filter} setFilter={f.setFilter} />
      )}
      <EtaCards
        orders={f.list}
        filterLabel={ETA_LABELS[f.filter]}
        selectedId={selectedTrackedId}
        onSelect={setSelectedTrackedId}
      />
      {showInspector && <EtaChat order={selected} />}
    </>
  );
}

interface UploadSectionProps {
  showSidebar: boolean;
  showInspector: boolean;
  selectedJobId: string;
  setSelectedJobId: (id: string) => void;
}

function UploadSection({
  showSidebar,
  showInspector,
  selectedJobId,
  setSelectedJobId,
}: UploadSectionProps) {
  return (
    <>
      {showSidebar && (
        <UploadJobs
          selectedJobId={selectedJobId}
          onSelect={setSelectedJobId}
        />
      )}
      <UploadWizard />
      {showInspector && <UploadInspector />}
    </>
  );
}

interface AgentsSectionProps {
  showSidebar: boolean;
  showInspector: boolean;
  selectedAgentId: string;
  setSelectedAgentId: (id: string) => void;
}

function AgentsSection({
  showSidebar,
  showInspector,
  selectedAgentId,
  setSelectedAgentId,
}: AgentsSectionProps) {
  return (
    <>
      {showSidebar && (
        <AgentsList
          selectedId={selectedAgentId}
          onSelect={setSelectedAgentId}
        />
      )}
      <AgentsPrompts agentId={selectedAgentId} />
      {showInspector && <AgentsInspector agentId={selectedAgentId} />}
    </>
  );
}
