/**
 * `MbaSection` — la Page del plugin mba (sección "Meta Business Agent").
 *
 * Misma estructura que la sección Agents: lista lateral de agentes MBA, canvas
 * con tabs por agente e inspector con el estado en Meta. Todo lo que muestra
 * sale de `/api/mba/*` (los archivos autorados del plugin).
 */
import { usePluginHost, useSelection } from "@/shared/lib";

import { MbaAgentsList } from "@plugins/mba/frontend/features/mba-agents-list";
import { MbaAgentCanvas } from "@plugins/mba/frontend/features/mba-agent-canvas";
import { MbaInspector } from "@plugins/mba/frontend/features/mba-inspector";

export function MbaSection() {
  const { showSidebar, showInspector } = usePluginHost();
  const [selectedRaw, setSelected] = useSelection("mba", "sales");
  const selectedId = selectedRaw ?? "sales";
  return (
    <>
      {showSidebar && <MbaAgentsList selectedId={selectedId} onSelect={setSelected} />}
      <MbaAgentCanvas agentId={selectedId} />
      {showInspector && <MbaInspector agentId={selectedId} />}
    </>
  );
}

export default MbaSection;
