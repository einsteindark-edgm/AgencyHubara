/**
 * Centro de Agentes: los 5 prompts read-only (Agents/Identity/Soul/Tools/Users)
 * del agente seleccionado, con el CONTENIDO REAL de los .md de su workspace
 * (servidos por GET /api/agents). Sin personalidades mockeadas.
 */

import { PROMPT_SECTIONS, useAgents } from "@/entities/agent";
import { Icon, type IconName } from "@/shared/ui";

interface Props {
  agentId: string;
}

export function AgentsPrompts({ agentId }: Props) {
  const { data: agents = [], isLoading, isError } = useAgents();

  const agent = agents.find((a) => a.id === agentId) ?? agents[0];

  if (!agent) {
    return (
      <main className="ag-canvas">
        <div style={{ padding: 32, color: "var(--fg-mute)", fontSize: 13 }}>
          {isLoading
            ? "Cargando agentes…"
            : isError
            ? "No se pudieron cargar los agentes."
            : "No hay agentes configurados."}
        </div>
      </main>
    );
  }

  const HeaderIcon = Icon[agent.icon as IconName] ?? Icon.wand;

  return (
    <main className="ag-canvas">
      <div className="ag-head">
        <div className={"ag-icon big-icon " + agent.color}>
          <HeaderIcon />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h1>{agent.name}</h1>
          <div className="desc">{agent.role}</div>
        </div>
      </div>

      <div className="ag-form">
        {PROMPT_SECTIONS.map((s) => {
          const prompt = agent.prompts.find((p) => p.key === s.key);
          const text = prompt?.content ?? "";
          const wc = prompt?.word_count ?? 0;
          const filename = prompt?.filename ?? `${s.key}.md`;
          const SectionIcon = Icon[s.icon as IconName];
          return (
            <div key={s.key} className="prompt-section">
              <div className="ps-head">
                <span className="ps-icon">
                  <SectionIcon />
                </span>
                <div className="ps-meta">
                  <h3>{s.title}</h3>
                  <p>{s.desc}</p>
                </div>
                <span className="ps-count">{wc} palabras</span>
              </div>
              <div className="prompt-view">
                <div className="prompt-bar">
                  <span className="pip">{filename}</span>
                </div>
                <div className="prompt-body" style={{ whiteSpace: "pre-wrap" }}>
                  {text || (
                    <span style={{ color: "var(--fg-faint)" }}>— sin contenido —</span>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </main>
  );
}
