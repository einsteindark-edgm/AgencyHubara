/**
 * Consola `agent_test` (D2.4): probar skills y conocimiento del agente contra
 * el simulador de Meta. No factura tokens ni toca hilos reales de WhatsApp.
 *
 * El hilo (turnos cliente/agente, `conversation_id`) es UI state del feature:
 * el simulador no tiene lectura de historial, cada turno es una mutation.
 * Un error (rechazo de Meta, caída, token faltante) queda inline en el hilo
 * con el motivo real; los turnos anteriores no se pierden.
 */
import { useReducer, useState } from "react";

import { useMbaAgentTest, type MbaAgentTestReply } from "@plugins/mba/frontend/entities/mba-agent-test";
import { ApiError } from "@/shared/api";
import { Icon, MacButton } from "@/shared/ui";

interface Props {
  agentId: string;
}

type Turn =
  | { kind: "user"; text: string }
  | { kind: "agent"; reply: MbaAgentTestReply }
  | { kind: "error"; text: string };

type Thread = { conversationId: string | null; turns: Turn[] };

type ThreadAction = { type: "user"; text: string } | { type: "agent"; reply: MbaAgentTestReply } | { type: "error"; text: string } | { type: "reset" };

function threadReducer(state: Thread, action: ThreadAction): Thread {
  switch (action.type) {
    case "user":
      return { ...state, turns: [...state.turns, { kind: "user", text: action.text }] };
    case "agent":
      return { conversationId: action.reply.conversation_id, turns: [...state.turns, { kind: "agent", reply: action.reply }] };
    case "error":
      return { ...state, turns: [...state.turns, { kind: "error", text: action.text }] };
    case "reset":
      return { conversationId: null, turns: [] };
  }
}

const ERROR_TEXT: Record<string, string> = {
  entity_id_missing: "El agente no tiene entity_id: onboardear el número primero (D3.1). Hasta entonces el simulador no tiene contra qué hablar.",
};

function apiMessage(e: unknown): string {
  if (e instanceof ApiError) {
    const body = e.body as { detail?: unknown } | null | undefined;
    const detail = body && typeof body === "object" ? body.detail : undefined;
    if (Array.isArray(detail)) return "entrada inválida (422)";
    if (detail && typeof detail === "object") {
      const d = detail as { error?: string; kind?: string; detail?: string };
      if (d.error && ERROR_TEXT[d.error]) return ERROR_TEXT[d.error];
      return [d.error, d.kind, d.detail].filter(Boolean).join(" · ");
    }
    if (typeof detail === "string") return detail;
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

function AgentBubble({ reply }: { reply: MbaAgentTestReply }) {
  const silent = !reply.agent_response.trim();
  return (
    <div style={{ ...bubble, alignSelf: "flex-start", background: "var(--bg-elev, rgba(127,127,127,0.12))" }}>
      <div style={{ fontSize: 10, color: "var(--fg-mute)", marginBottom: 2 }}>MBA</div>
      {silent ? (
        <div style={{ fontStyle: "italic", color: "var(--fg-mute)" }}>
          sin respuesta{reply.no_response_reason ? `: ${reply.no_response_reason}` : ""}
        </div>
      ) : (
        <div style={{ whiteSpace: "pre-wrap" }}>{reply.agent_response}</div>
      )}
      {reply.handoff_reason && (
        <div style={{ fontSize: 11, color: "var(--color-warning, #d97706)", marginTop: 4 }}>handoff: {reply.handoff_reason}</div>
      )}
      {reply.quick_replies && reply.quick_replies.length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6 }}>
          {reply.quick_replies.map((q, i) => (
            <span key={`${i}:${q}`} style={chip}>{q}</span>
          ))}
        </div>
      )}
      {reply.product_variant_ids && reply.product_variant_ids.length > 0 && (
        <div className="mono" style={{ fontSize: 10, color: "var(--fg-mute)", marginTop: 4 }}>
          variantes: {reply.product_variant_ids.join(", ")}
        </div>
      )}
    </div>
  );
}

export function MbaAgentTestConsole({ agentId }: Props) {
  const [thread, dispatch] = useReducer(threadReducer, { conversationId: null, turns: [] });
  const [draft, setDraft] = useState("");
  const turn = useMbaAgentTest(agentId);
  const busy = turn.isPending;

  const onSend = () => {
    const text = draft.trim();
    if (!text || busy) return;
    dispatch({ type: "user", text });
    setDraft("");
    turn.mutate(
      { message: text, conversationId: thread.conversationId },
      {
        onSuccess: (out) => {
          if (out.ok && out.reply) dispatch({ type: "agent", reply: out.reply });
          else dispatch({ type: "error", text: `Meta rechazó el turno: ${out.error?.detail ?? out.error?.kind ?? "sin detalle"}` });
        },
        onError: (e) => dispatch({ type: "error", text: `No se pudo hablar con Meta: ${apiMessage(e)}` }),
      },
    );
  };

  return (
    <div className="ag-form" style={{ display: "flex", flexDirection: "column", gap: 10, maxWidth: 760 }}>
      <div style={{ fontSize: 12.5, color: "var(--fg-muted)" }}>
        Simulador de Meta (<code>agent_test</code>): responde con las skills, el conocimiento y las UI skills ya
        sincronizadas. No factura tokens ni toca conversaciones reales de WhatsApp. Las connector tools sí pueden
        ejecutarse contra el API de Hubara.
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12, color: "var(--fg-mute)" }}>
        <span>
          Conversación: <span className="mono">{thread.conversationId ?? "nueva (sin conversation_id)"}</span>
        </span>
        <MacButton sm ghost disabled={busy} onClick={() => dispatch({ type: "reset" })}>
          <Icon.workflow /> Nueva conversación
        </MacButton>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8, minHeight: 120 }}>
        {thread.turns.length === 0 && (
          <div style={{ fontSize: 12, color: "var(--fg-mute)" }}>Todavía no hay turnos. Escribí como si fueras el cliente.</div>
        )}
        {thread.turns.map((t, i) =>
          t.kind === "user" ? (
            <div key={i} style={{ ...bubble, alignSelf: "flex-end", background: "var(--accent-soft, rgba(37,99,235,0.14))" }}>
              <div style={{ fontSize: 10, color: "var(--fg-mute)", marginBottom: 2 }}>cliente</div>
              <div style={{ whiteSpace: "pre-wrap" }}>{t.text}</div>
            </div>
          ) : t.kind === "agent" ? (
            <AgentBubble key={i} reply={t.reply} />
          ) : (
            <div key={i} role="alert" style={{ ...bubble, alignSelf: "stretch", ...errStyle }}>
              {t.text}
            </div>
          ),
        )}
        {busy && (
          <div role="status" style={{ fontSize: 12, color: "var(--fg-mute)" }}>MBA está escribiendo…</div>
        )}
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              onSend();
            }
          }}
          placeholder="Escribí como el cliente… (Enter envía, Shift+Enter salto de línea)"
          aria-label="Mensaje del cliente para el simulador"
          rows={2}
          maxLength={4096}
          style={inputStyle}
        />
        <MacButton sm primary disabled={busy || !draft.trim()} onClick={onSend}>
          Enviar
        </MacButton>
      </div>
    </div>
  );
}

const bubble: React.CSSProperties = {
  maxWidth: "80%",
  padding: "0.45rem 0.65rem",
  borderRadius: 10,
  fontSize: 12.5,
  lineHeight: 1.4,
};
const chip: React.CSSProperties = {
  padding: "2px 8px",
  borderRadius: 999,
  border: "1px solid var(--border, rgba(127,127,127,0.35))",
  fontSize: 11,
};
const inputStyle: React.CSSProperties = {
  flex: 1,
  padding: "0.45rem 0.6rem",
  borderRadius: 8,
  border: "1px solid var(--border, rgba(127,127,127,0.3))",
  background: "var(--bg, transparent)",
  color: "inherit",
  fontSize: 12.5,
  resize: "vertical",
  fontFamily: "inherit",
};
const errStyle: React.CSSProperties = {
  background: "rgba(255,114,105,0.14)",
  border: "1px solid rgba(255,114,105,0.4)",
  color: "var(--color-danger)",
  fontSize: 12,
};
