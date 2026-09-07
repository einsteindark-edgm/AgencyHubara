/**
 * Sidebar de la sección Meta Business Agent: los agentes MBA autorados en el
 * plugin. La selección la maneja la página (cross-feature con el canvas y el
 * inspector). Misma estructura visual que la lista de Agentes.
 */
import { useMbaAgents, type MbaAgent } from "@plugins/mba/frontend/entities/mba-agent";
import { Icon, type IconName } from "@/shared/ui";

interface Props {
  selectedId: string;
  onSelect: (id: string) => void;
}

export function MbaAgentsList({ selectedId, onSelect }: Props) {
  const { data: agents = [], isError } = useMbaAgents();
  return (
    <aside className="sidebar">
      <div className="side-header">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 14, fontWeight: 700, letterSpacing: "-0.01em" }}>
            Meta Business Agent
          </span>
          <span
            style={{
              fontSize: 10,
              color: "var(--fg-faint)",
              background: "rgba(255,255,255,0.05)",
              padding: "1px 5px",
              borderRadius: 4,
            }}
          >
            {agents.length}
          </span>
        </div>
      </div>

      <div className="ag-list">
        {isError && (
          <div style={{ padding: "10px 14px", fontSize: 12, color: "var(--warn, #d97706)" }}>
            No se pudieron cargar los agentes de Meta Business Agent.
          </div>
        )}
        <div className="side-section">
          <span className="caret">
            <Icon.caret />
          </span>
          agentes en Meta
          <span className="ct">{agents.length}</span>
        </div>
        {agents.map((a) => (
          <AgentRow key={a.id} agent={a} selected={selectedId === a.id} onSelect={onSelect} />
        ))}
      </div>
    </aside>
  );
}

function AgentRow({
  agent,
  selected,
  onSelect,
}: {
  agent: MbaAgent;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const IconComp = Icon[agent.icon as IconName] ?? Icon.bot;
  return (
    <div className={"ag-row" + (selected ? " sel" : "")} onClick={() => onSelect(agent.id)}>
      <div className={"ag-icon " + agent.color}>
        <IconComp />
      </div>
      <div className="ag-body">
        <span className="ag-name">{agent.display_name}</span>
        <span className="ag-sub">{agent.role}</span>
      </div>
    </div>
  );
}
