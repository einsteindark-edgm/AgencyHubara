/**
 * `AgentsSection` — la Page del plugin agents_admin.
 *
 * Extraída de `pages/Dashboard.tsx` en PR4. Mantiene la firma de props que
 * tenía cuando vivía inline en el shell.
 *
 * Plugin frontend-only (sin agente Temporal, sin worker, sin endpoints de API
 * propios — los datos vienen de `entities/agent` que es shared cross-plugin).
 */
import { usePluginHost, useSelection } from "@/shared/lib";

import { AgentsList } from "@plugins/agents_admin/frontend/features/agents-list";
import { AgentsPrompts } from "@plugins/agents_admin/frontend/features/agents-prompts";
import { AgentsInspector } from "@plugins/agents_admin/frontend/features/agents-inspector";
import { PerceptionRolloutPanel } from "@plugins/agents_admin/frontend/features/perception-rollout";

export function AgentsSection() {
  // F7: chrome + selección llegan por el PluginHost (contrato genérico).
  const { showSidebar, showInspector } = usePluginHost();
  const [selectedAgentIdRaw, setSelectedAgentId] = useSelection(
    "agents_admin",
    "sales",
  );
  const selectedAgentId = selectedAgentIdRaw ?? "sales";
  return (
    <>
      {showSidebar && (
        <AgentsList
          selectedId={selectedAgentId}
          onSelect={setSelectedAgentId}
        />
      )}
      <AgentsPrompts agentId={selectedAgentId} />
      {showInspector && (
        <AgentsInspector agentId={selectedAgentId}>
          {/* El bot nuevo (capas con clasificador) solo existe en ventas. */}
          {selectedAgentId === "sales" && <PerceptionRolloutPanel />}
        </AgentsInspector>
      )}
    </>
  );
}

export default AgentsSection;
