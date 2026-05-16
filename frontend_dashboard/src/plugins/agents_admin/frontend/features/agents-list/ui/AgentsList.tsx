/**
 * Sidebar de Agentes IA — agrupados por categoría (Ventas, Filtro, Remarketing,
 * Soporte, Cobranza, Onboarding, Encuestas, Interno). El estado de selección
 * lo pasa la página (cross-feature con AgentsPrompts + AgentsInspector).
 */

import { useState } from "react";
import { useAgents, type Agent } from "@/entities/agent";
import { Icon, type IconName } from "@/shared/ui";

const FILTERS = ["Todos", "Activos", "Borradores", "Pausados"];

interface Props {
  selectedId: string;
  onSelect: (id: string) => void;
}

export function AgentsList({ selectedId, onSelect }: Props) {
  const { data: agents = [] } = useAgents();
  const [filter, setFilter] = useState("Todos");

  const groups = new Map<string, Agent[]>();
  agents.forEach((a) => {
    const arr = groups.get(a.category) ?? [];
    arr.push(a);
    groups.set(a.category, arr);
  });

  return (
    <aside className="sidebar">
      <div className="side-header">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 14, fontWeight: 700, letterSpacing: "-0.01em" }}>
            Agentes IA
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

        <div className="side-search">
          <Icon.search />
          <input placeholder="Buscar agentes…" />
        </div>

        <div className="side-tabs">
          {FILTERS.map((f) => (
            <button
              key={f}
              className={"pill" + (filter === f ? " on" : "")}
              onClick={() => setFilter(f)}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      <div className="ag-list">
        {[...groups.entries()].map(([cat, arr]) => (
          <div key={cat}>
            <div className="side-section">
              <span className="caret"><Icon.caret /></span>
              {cat}
              <span className="ct">{arr.length}</span>
            </div>
            {arr.map((a) => (
              <AgentRow
                key={a.id}
                agent={a}
                selected={selectedId === a.id}
                onSelect={onSelect}
              />
            ))}
          </div>
        ))}
      </div>
    </aside>
  );
}

interface RowProps {
  agent: Agent;
  selected: boolean;
  onSelect: (id: string) => void;
}

function AgentRow({ agent, selected, onSelect }: RowProps) {
  const IconComp = Icon[agent.icon as IconName];
  return (
    <div
      className={"ag-row" + (selected ? " sel" : "")}
      onClick={() => onSelect(agent.id)}
    >
      <div className={"ag-icon " + agent.color}>
        <IconComp />
      </div>
      <div className="ag-body">
        <span className="ag-name">{agent.name}</span>
        <span className="ag-sub">{agent.role}</span>
        <span className="ag-meta">
          <span
            className={
              "pulse " +
              (agent.status === "idle"
                ? "idle"
                : agent.status === "off"
                ? "off"
                : "")
            }
          />
          {agent.calls.toLocaleString()} sesiones
          {agent.csat != null && (
            <>
              <span style={{ color: "var(--fg-faint)" }}>·</span>★ {agent.csat}
            </>
          )}
        </span>
      </div>
    </div>
  );
}
