/**
 * Canvas central de Agentes. Tabs por agente:
 *   - "Personalidad": los 5 prompts read-only (Agents/Identity/Soul/Tools/Users)
 *     con el CONTENIDO REAL de los .md de su workspace (GET /api/agents).
 *   - "Meta Business Agent": qué le enviaríamos a MBA para este agente
 *     (skills, business_info, FAQs, settings), normalizado desde esos mismos
 *     archivos por el backend (GET /api/agents/{id}/mba-config).
 *   - "Calidad LLM" (solo `sales`): el panel de evaluación (tendencia + curación
 *     de goldens), compuesto desde `agents-quality`.
 */

import { useState } from "react";

import { PROMPT_SECTIONS, useAgents } from "@plugins/agents_admin/frontend/entities/agent";
import { AgentsMbaPreview } from "@plugins/agents_admin/frontend/features/agents-mba-preview";
import { AgentsQuality } from "@plugins/agents_admin/frontend/features/agents-quality";
import { Icon, type IconName } from "@/shared/ui";

type CanvasTab = "personalidad" | "mba" | "calidad";

interface Props {
  agentId: string;
}

export function AgentsPrompts({ agentId }: Props) {
  const { data: agents = [], isLoading, isError } = useAgents();

  const agent = agents.find((a) => a.id === agentId) ?? agents[0];

  // Tab del canvas. Los hooks van antes del early-return (Rules of Hooks). El
  // tab elegido se guarda junto al agente al que pertenece: al cambiar de
  // agente vuelve a "personalidad" sin un effect (patrón "derivar del prop").
  const [tabFor, setTabFor] = useState<{ agentId: string | undefined; tab: CanvasTab }>({
    agentId: undefined,
    tab: "personalidad",
  });
  const tab: CanvasTab = tabFor.agentId === agent?.id ? tabFor.tab : "personalidad";
  const setTab = (next: CanvasTab) => setTabFor({ agentId: agent?.id, tab: next });

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

  const isSales = agent.id === "sales";
  // En "Calidad LLM" ocultamos el header del agente (icono + nombre + rol): qué
  // agente es ya se sabe por la selección de la barra izquierda, y esos ~70px se
  // los damos a la vista de episodios evaluados, que es densa (chart + lista +
  // detalle). En "Personalidad" el header se mantiene.
  const onQuality = isSales && tab === "calidad";
  const HeaderIcon = Icon[agent.icon as IconName] ?? Icon.wand;

  return (
    <main className="ag-canvas">
      {!onQuality && (
        <div className="ag-head">
          <div className={"ag-icon big-icon " + agent.color}>
            <HeaderIcon />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h1>{agent.name}</h1>
            <div className="desc">{agent.role}</div>
          </div>
        </div>
      )}

      <div className="sub-tabs">
        <button
          type="button"
          className={"sub-tab" + (tab === "personalidad" ? " on" : "")}
          onClick={() => setTab("personalidad")}
        >
          <Icon.wand /> Personalidad
        </button>
        <button
          type="button"
          className={"sub-tab" + (tab === "mba" ? " on" : "")}
          onClick={() => setTab("mba")}
        >
          <Icon.bot /> Meta Business Agent
        </button>
        {isSales && (
          <button
            type="button"
            className={"sub-tab" + (tab === "calidad" ? " on" : "")}
            onClick={() => setTab("calidad")}
          >
            <Icon.shield /> Calidad LLM
          </button>
        )}
      </div>

      {onQuality ? (
        <AgentsQuality />
      ) : tab === "mba" ? (
        <AgentsMbaPreview agentId={agent.id} />
      ) : (
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
      )}
    </main>
  );
}
