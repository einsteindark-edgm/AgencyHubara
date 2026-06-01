import { useState } from "react";
import { useAgents } from "@/entities/agent";
import { AgentsList } from "@plugins/agents_admin/frontend/features/agents-list";
import { AgentsPrompts } from "@plugins/agents_admin/frontend/features/agents-prompts";
import { AgentsInspector } from "@plugins/agents_admin/frontend/features/agents-inspector";

export interface AgentsSectionProps {
  showSidebar: boolean;
  showInspector: boolean;
}

export function AgentsSection({ showSidebar, showInspector }: AgentsSectionProps) {
  const { data: agents = [] } = useAgents();
  const [selectedId, setSelectedId] = useState<string>("");
  const activeId = selectedId || (agents[0]?.id ?? "");
  const activeAgent = agents.find((a) => a.id === activeId) ?? agents[0];

  return (
    <>
      {showSidebar && (
        <AgentsList
          selectedId={activeId}
          onSelect={setSelectedId}
        />
      )}
      {activeAgent && <AgentsPrompts agent={activeAgent} />}
      {showInspector && activeAgent && <AgentsInspector agent={activeAgent} />}
    </>
  );
}

export default AgentsSection;
