/**
 * Canvas central de Meta Business Agent. Misma estructura que el canvas de
 * Agentes (cabecera + sub-tabs), con la información REAL del agente en Meta:
 *   - "Configuración": lo que se envía a Meta, request por request, con el
 *     sync (D2.2) y el rollout (D2.3) arriba.
 *   - "Agent test": la consola del simulador de Meta (D2.4).
 *   - "Insights", "Agent eval": todavía no construidas, deshabilitadas para
 *     fijar la estructura.
 */
import { useState } from "react";

import { useMbaAgents } from "@plugins/mba/frontend/entities/mba-agent";
import { MbaAgentTestConsole } from "@plugins/mba/frontend/features/mba-agent-test";
import { MbaConfigPreview } from "@plugins/mba/frontend/features/mba-config-preview";
import { MbaRolloutPanel } from "@plugins/mba/frontend/features/mba-rollout";
import { MbaSyncPanel } from "@plugins/mba/frontend/features/mba-sync";
import { Icon, type IconName } from "@/shared/ui";

type CanvasTab = "configuracion" | "insights" | "agent_test" | "agent_eval";

const FUTURE_TABS: { key: CanvasTab; label: string; icon: IconName }[] = [
  { key: "insights", label: "Insights", icon: "spark" },
  { key: "agent_eval", label: "Agent eval", icon: "shield" },
];

interface Props {
  agentId: string;
}

export function MbaAgentCanvas({ agentId }: Props) {
  const { data: agents = [], isLoading, isError } = useMbaAgents();
  const agent = agents.find((a) => a.id === agentId) ?? agents[0];
  const [tab, setTab] = useState<CanvasTab>("configuracion");

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
        <button
          type="button"
          className={"sub-tab" + (tab === "configuracion" ? " on" : "")}
          onClick={() => setTab("configuracion")}
        >
          <Icon.notes /> Configuración
        </button>
        <button
          type="button"
          className={"sub-tab" + (tab === "agent_test" ? " on" : "")}
          onClick={() => setTab("agent_test")}
        >
          <Icon.bolt /> Agent test
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

      {tab === "agent_test" ? (
        <MbaAgentTestConsole agentId={agent.id} />
      ) : (
        <>
          <div className="ag-form" style={{ paddingBottom: 0 }}>
            <MbaSyncPanel agentId={agent.id} />
            <MbaRolloutPanel agentId={agent.id} />
          </div>
          <MbaConfigPreview agentId={agent.id} />
        </>
      )}
    </main>
  );
}
