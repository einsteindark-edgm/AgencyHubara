/**
 * Canvas central de Meta Business Agent. Misma estructura que el canvas de
 * Agentes (cabecera + sub-tabs), con la información REAL del agente en Meta:
 *   - "Configuración": lo que se envía a Meta, request por request.
 *   - "Insights", "Agent test", "Agent eval": las superficies de operación de
 *     la Platform (agent-insights, agent_test, agent-eval). Todavía no están
 *     construidas: se muestran deshabilitadas para fijar la estructura.
 */
import { useState } from "react";

import { useMbaAgents } from "@plugins/mba/frontend/entities/mba-agent";
import { MbaConfigPreview } from "@plugins/mba/frontend/features/mba-config-preview";
import { Icon, type IconName } from "@/shared/ui";

type CanvasTab = "configuracion" | "insights" | "agent_test" | "agent_eval";

const FUTURE_TABS: { key: CanvasTab; label: string; icon: IconName }[] = [
  { key: "insights", label: "Insights", icon: "spark" },
  { key: "agent_test", label: "Agent test", icon: "bolt" },
  { key: "agent_eval", label: "Agent eval", icon: "shield" },
];

interface Props {
  agentId: string;
}

export function MbaAgentCanvas({ agentId }: Props) {
  const { data: agents = [], isLoading, isError } = useMbaAgents();
  const agent = agents.find((a) => a.id === agentId) ?? agents[0];
  // Un solo tab construido por ahora; el estado queda listo para los siguientes.
  const [tab] = useState<CanvasTab>("configuracion");

  if (!agent) {
    return (
      <main className="ag-canvas">
        <div style={{ padding: 32, color: "var(--fg-mute)", fontSize: 13 }}>
          {isLoading
            ? "Cargando agentes…"
            : isError
              ? "No se pudieron cargar los agentes de Meta Business Agent."
              : "No hay agentes MBA configurados."}
        </div>
      </main>
    );
  }

  const HeaderIcon = Icon[agent.icon as IconName] ?? Icon.bot;

  return (
    <main className="ag-canvas">
      <div className="ag-head">
        <div className={"ag-icon big-icon " + agent.color}>
          <HeaderIcon />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h1>{agent.display_name}</h1>
          <div className="desc">{agent.role}</div>
        </div>
      </div>

      <div className="sub-tabs">
        <button type="button" className={"sub-tab" + (tab === "configuracion" ? " on" : "")}>
          <Icon.notes /> Configuración
        </button>
        {FUTURE_TABS.map((t) => {
          const TabIcon = Icon[t.icon];
          return (
            <button
              key={t.key}
              type="button"
              className="sub-tab"
              disabled
              title="Próximamente"
              style={{ opacity: 0.45, cursor: "not-allowed" }}
            >
              <TabIcon /> {t.label}
            </button>
          );
        })}
      </div>

      <MbaConfigPreview agentId={agent.id} />
    </main>
  );
}
