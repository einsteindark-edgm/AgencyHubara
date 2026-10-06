/**
 * Inspector de Agentes — Modelo + Capacidades, en modo lectura.
 * Datos reales declarados en el manifest del agente (no editable desde la UI).
 * `children`: paneles extra que compone la página (una feature no importa a
 * otra), p. ej. el encendido del bot nuevo cuando el agente es ventas.
 */

import type { ReactNode } from "react";

import { useAgents } from "@plugins/agents_admin/frontend/entities/agent";
import { Icon, Panel, type IconName } from "@/shared/ui";

interface Props {
  agentId: string;
  children?: ReactNode;
}

export function AgentsInspector({ agentId, children }: Props) {
  const { data: agents = [] } = useAgents();
  const agent = agents.find((a) => a.id === agentId) ?? agents[0];
  if (!agent) return null;

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
            <span className="val mono">{agent.model ?? "—"}</span>
          </div>
        </Panel>

        {agent.capabilities.length > 0 && (
          <Panel title="Capacidades">
            {agent.capabilities.map((c, i) => {
              const Comp = Icon[c.icon as IconName] ?? Icon.bolt;
              return (
                <div key={i} className="cap-row">
                  <span className="cico">
                    <Comp />
                  </span>
                  <span style={{ flex: 1 }}>
                    <div className="cn">{c.label}</div>
                  </span>
                </div>
              );
            })}
          </Panel>
        )}

        {children}
      </div>
    </aside>
  );
}
