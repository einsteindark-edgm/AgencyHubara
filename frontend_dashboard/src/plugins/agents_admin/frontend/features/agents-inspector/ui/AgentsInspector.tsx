import { type Agent } from "@/entities/agent";
import { Icon, Panel, type IconName } from "@/shared/ui";

interface Props {
  agent: Agent;
}

export function AgentsInspector({ agent }: Props) {
  return (
    <aside className="inspector">
      <div className="insp-body">
        <div style={{ padding: "12px 14px 8px" }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: "-0.01em" }}>
            Detalles
          </div>
          <div style={{ fontSize: 11, color: "var(--fg-mute)", marginTop: 2 }}>
            {agent.name}
          </div>
        </div>

        <Panel title="Modelo">
          <div className="form-row">
            <span className="lbl">Modelo</span>
            <span className="val mono">{agent.model}</span>
          </div>
        </Panel>

        <Panel title="Capacidades">
          {agent.capabilities.map((c, i) => {
            const Comp = Icon[c.icon as IconName];
            return (
              <div key={i} className="cap-row">
                <span className="cico">
                  <Comp />
                </span>
                <span style={{ flex: 1 }}>
                  <div className="cn">{c.name}</div>
                </span>
              </div>
            );
          })}
        </Panel>
      </div>
    </aside>
  );
}
