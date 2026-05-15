/**
 * Inspector derecho de Chats con 3 tabs: Tag (perfil histórico), Agente actual,
 * Memoria IA. Cada tab renderiza sub-paneles colapsables.
 */

import { useState } from "react";
import {
  useChatInbox,
  useChatMemory,
  useChatRoutingLog,
} from "@/entities/chat";
import { Icon, Panel } from "@/shared/ui";

type InspectorTab = "tag" | "agent" | "mem";

const TITLES: Record<InspectorTab, { h: string; s: string }> = {
  tag:   { h: "Perfil de tags histórico", s: "Movimientos de etiquetas en esta sesión" },
  agent: { h: "Agente actual",            s: "Quién está atendiendo esta conversación" },
  mem:   { h: "Memoria de la IA",         s: "Lo que recuerda el modelo sobre este contacto" },
};

interface Props {
  chatId: string | null;
}

export function ChatsInspector({ chatId }: Props) {
  const [tab, setTab] = useState<InspectorTab>("tag");
  const meta = TITLES[tab];

  return (
    <aside className="inspector">
      <div className="insp-tabs">
        <button
          className={"insp-tab" + (tab === "tag" ? " on" : "")}
          title="Perfil de tags"
          onClick={() => setTab("tag")}
        >
          <Icon.tag />
        </button>
        <button
          className={"insp-tab" + (tab === "agent" ? " on" : "")}
          title="Agente actual"
          onClick={() => setTab("agent")}
        >
          <Icon.user />
        </button>
        <button
          className={"insp-tab" + (tab === "mem" ? " on" : "")}
          title="Memoria IA"
          onClick={() => setTab("mem")}
        >
          <Icon.shield />
        </button>
      </div>

      <div className="insp-body">
        <div style={{ padding: "12px 14px 8px" }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: "-0.01em" }}>
            {meta.h}
          </div>
          <div style={{ fontSize: 11, color: "var(--fg-mute)", marginTop: 2 }}>
            {meta.s}
          </div>
        </div>

        {tab === "tag" && <TagsTab chatId={chatId} />}
        {tab === "agent" && <AgentTab />}
        {tab === "mem" && <MemoryTab chatId={chatId} />}
      </div>
    </aside>
  );
}

function TagsTab({ chatId }: { chatId: string | null }) {
  const { data: chats = [] } = useChatInbox();
  const { data: log = [] } = useChatRoutingLog(chatId);
  const chat = chats.find((c) => c.id === chatId);
  const tagLabel = chat ? chat.tag : "—";

  return (
    <>
      <Panel
        title="Estado actual"
        actions={
          <>
            <button className="ico" title="Editar">
              <Icon.edit />
            </button>
            <button className="ico" title="Copiar">
              <Icon.copy />
            </button>
          </>
        }
      >
        <span className="tag-big">Tag actual · {tagLabel}</span>
        <div style={{ marginTop: 10 }}>
          <div className="form-row">
            <span className="lbl">ID de sesión</span>
            <span className="val mono">ses_a3f8c2…</span>
          </div>
          <div className="form-row">
            <span className="lbl">Iniciada</span>
            <span className="val">7/5, 4:30 p.m.</span>
          </div>
          <div className="form-row">
            <span className="lbl">Origen</span>
            <span className="val link">Meta Ads · velas</span>
          </div>
        </div>
        <div className="quick-row" style={{ marginTop: 10 }}>
          <button className="insp-button">
            <Icon.user />
            Reasignar
          </button>
        </div>
      </Panel>

      <Panel
        title="Perfil de tags histórico"
        actions={
          <button className="ico" title="Filtrar">
            <Icon.filter />
          </button>
        }
      >
        <div className="log">
          {log.map((l, i) => (
            <div key={i} className={"log-item " + l.color}>
              <div className="log-head">
                <span className={"log-tag " + l.tagClass}>{l.tag}</span>
                <span style={{ color: "var(--fg-mute)" }}>·</span>
                <span className="agent">{l.agent}</span>
                <span className="time">{l.time}</span>
              </div>
              <div className="log-body">{l.body}</div>
            </div>
          ))}
        </div>
      </Panel>
    </>
  );
}

function AgentTab() {
  return (
    <Panel title="Detalles del agente">
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <div
          style={{
            width: 36, height: 36, borderRadius: "50%",
            background: "rgba(255,255,255,0.06)",
            display: "flex", alignItems: "center", justifyContent: "center",
            color: "var(--fg-soft)",
          }}
        >
          <Icon.user />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>remarketing</div>
          <div
            style={{
              fontSize: 11,
              color: "var(--green)",
              display: "flex",
              alignItems: "center",
              gap: 5,
            }}
          >
            <span
              style={{
                width: 6, height: 6, borderRadius: "50%",
                background: "currentColor",
              }}
            />
            Active routing handler
          </div>
        </div>
      </div>
      <div className="form-row">
        <span className="lbl">Plataforma</span>
        <span className="val">WhatsApp API</span>
      </div>
      <div className="form-row">
        <span className="lbl">Modelo</span>
        <span className="val mono">claude-haiku-4-5</span>
      </div>
      <div className="form-row">
        <span className="lbl">Temperatura</span>
        <span className="val">0.4</span>
      </div>
      <div className="form-row">
        <span className="lbl">Tokens</span>
        <span className="val">12,840 / 200k</span>
      </div>
      <div className="btn-grid" style={{ marginTop: 10 }}>
        <button className="insp-button">
          <Icon.edit />
          Prompt
        </button>
        <button className="insp-button">
          <Icon.workflow />
          Flujo
        </button>
        <button className="insp-button">
          <Icon.bolt />
          Probar
        </button>
        <button className="insp-button">
          <Icon.copy />
          Clonar
        </button>
      </div>
    </Panel>
  );
}

function MemoryTab({ chatId }: { chatId: string | null }) {
  const { data: memory = [] } = useChatMemory(chatId);
  return (
    <Panel
      title="Memoria IA"
      actions={
        <>
          <button className="ico" title="Refrescar">
            <Icon.refresh />
          </button>
          <button className="ico" title="Editar">
            <Icon.edit />
          </button>
        </>
      }
    >
      {memory.map((m, i) => (
        <div key={i} className="memo">
          <div className="h">
            <span className="k">{m.key}</span>
            <span className="v">{m.value}</span>
          </div>
          <p>{m.body}</p>
        </div>
      ))}
      <button className="insp-button primary full" style={{ marginTop: 6 }}>
        <Icon.wand />
        Visualizar resumen completo
      </button>
    </Panel>
  );
}
