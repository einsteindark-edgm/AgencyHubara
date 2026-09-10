/**
 * Inspector derecho de Chats con 3 tabs: Tag (perfil histórico), Agente actual,
 * Memoria IA. Cada tab renderiza sub-paneles colapsables.
 */

import { useState } from "react";
import {
  useChatInbox,
  useChatMemory,
  useChatOverview,
  useChatRoutingLog,
} from "@plugins/chats/frontend/entities/chat";
import {
  OPERATOR_TAGS,
  OPERATOR_TAG_LABELS,
  useReassignTagMutation,
  type OperatorTag,
} from "@plugins/chats/frontend/entities/session-tag";
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
  const { data: overview } = useChatOverview(chatId);
  const chat = chats.find((c) => c.id === chatId);
  const tagLabel = chat ? chat.tag : "—";
  const [reassigning, setReassigning] = useState(false);

  return (
    <>
      <Panel
        title="Estado actual"
        actions={
          <>
            <button className="ico" title="Editar" onClick={() => setReassigning(true)}>
              <Icon.edit />
            </button>
            <button
              className="ico"
              title="Copiar"
              onClick={() => {
                if (chatId) void navigator.clipboard?.writeText(chatId);
              }}
            >
              <Icon.copy />
            </button>
          </>
        }
      >
        <span className="tag-big">Tag actual · {tagLabel}</span>
        <div style={{ marginTop: 10 }}>
          <div className="form-row">
            <span className="lbl">ID de sesión</span>
            <span className="val mono">{overview?.sessionId ?? chatId ?? "—"}</span>
          </div>
          <div className="form-row">
            <span className="lbl">Iniciada</span>
            <span className="val">{overview?.startedLabel ?? "—"}</span>
          </div>
          <div className="form-row">
            <span className="lbl">Origen</span>
            <span
              className={"val" + (overview?.originIsMeta ? " link" : "")}
              title={overview?.originDetail}
            >
              {overview?.originLabel ?? "—"}
            </span>
          </div>
          {overview?.originDetail && (
            <div className="form-row">
              <span className="lbl">Anuncio</span>
              <span className="val">{overview.originDetail}</span>
            </div>
          )}
        </div>
        {reassigning ? (
          <ReassignTagForm chatId={chatId} onDone={() => setReassigning(false)} />
        ) : (
          <div className="quick-row" style={{ marginTop: 10 }}>
            <button
              className="insp-button"
              onClick={() => setReassigning(true)}
              disabled={!chatId}
            >
              <Icon.user />
              Reasignar
            </button>
          </div>
        )}
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

/** Formulario inline de "Reasignar": el operador fija el tag (decisión, no
 *  propuesta) con un motivo obligatorio que queda en el historial. */
function ReassignTagForm({
  chatId,
  onDone,
}: {
  chatId: string | null;
  onDone: () => void;
}) {
  const [tag, setTag] = useState<OperatorTag>("INTERESADO");
  const [motivo, setMotivo] = useState("");
  const mutation = useReassignTagMutation(chatId);
  const canSave = Boolean(chatId) && motivo.trim().length > 0 && !mutation.isPending;

  const fieldStyle = {
    width: "100%",
    background: "rgba(255,255,255,0.04)",
    color: "var(--fg)",
    border: "1px solid var(--border)",
    borderRadius: 6,
    padding: "6px 8px",
    fontSize: 12,
  } as const;

  return (
    <form
      style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6 }}
      onSubmit={(e) => {
        e.preventDefault();
        if (!canSave) return;
        mutation.mutate(
          { tag, motivo: motivo.trim() },
          { onSuccess: () => onDone() },
        );
      }}
    >
      <label className="lbl" htmlFor="reassign-tag" style={{ fontSize: 11.5 }}>
        Nuevo tag
      </label>
      <select
        id="reassign-tag"
        style={fieldStyle}
        value={tag}
        onChange={(e) => setTag(e.target.value as OperatorTag)}
      >
        {OPERATOR_TAGS.map((t) => (
          <option key={t} value={t}>
            {OPERATOR_TAG_LABELS[t]}
          </option>
        ))}
      </select>
      <label className="lbl" htmlFor="reassign-motivo" style={{ fontSize: 11.5 }}>
        Motivo
      </label>
      <textarea
        id="reassign-motivo"
        style={{ ...fieldStyle, minHeight: 52, resize: "vertical" }}
        placeholder="Por qué cambia el tag (queda en el historial)"
        value={motivo}
        onChange={(e) => setMotivo(e.target.value)}
      />
      {mutation.isError && (
        <div style={{ color: "#ff6b62", fontSize: 11.5 }}>
          No se pudo reasignar: {mutation.error.message}
        </div>
      )}
      <div className="quick-row">
        <button type="submit" className="insp-button primary" disabled={!canSave}>
          {mutation.isPending ? "Guardando…" : "Guardar"}
        </button>
        <button type="button" className="insp-button" onClick={onDone}>
          Cancelar
        </button>
      </div>
    </form>
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
