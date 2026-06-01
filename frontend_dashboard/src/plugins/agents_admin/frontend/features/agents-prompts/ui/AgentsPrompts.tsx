import { type Agent, PROMPT_SECTIONS } from "@/entities/agent";
import { wordCount } from "@/shared/lib";
import { Icon, type IconName } from "@/shared/ui";

interface Props {
  agent: Agent;
}

export function AgentsPrompts({ agent }: Props) {
  const HeaderIcon = Icon[agent.icon as IconName];

  return (
    <main className="ag-canvas">
      <div className="ag-head">
        <div className={"ag-icon big-icon " + agent.color}>
          <HeaderIcon />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h1>
            {agent.name}
            <span className="ver">v1.4.2</span>
            <span
              style={{
                fontSize: 11,
                color: "var(--green)",
                fontWeight: 500,
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: "currentColor",
                  boxShadow: "0 0 0 2px rgba(48,209,88,0.18)",
                }}
              />
              Activo
            </span>
          </h1>
          <div className="desc">{agent.role}</div>
        </div>
      </div>

      <div className="ag-form">
        {PROMPT_SECTIONS.map((s) => {
          const text = agent.workspace[s.key] ?? "";
          const wc = wordCount(text);
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
                  <span className="pip">{s.key}.md</span>
                </div>
                <div className="prompt-body">{text}</div>
              </div>
            </div>
          );
        })}
        {agent.workspace.skills.map((skill) => (
          <div key={skill.name} className="prompt-section">
            <div className="ps-head">{skill.name}</div>
            <div className="prompt-view">{skill.content}</div>
          </div>
        ))}
      </div>
    </main>
  );
}
