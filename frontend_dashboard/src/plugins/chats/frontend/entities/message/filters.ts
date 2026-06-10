/**
 * Predicados de visibilidad. Antes vivían inline dentro de `CenterCol_ChatView`,
 * lo que los hacía no-reutilizables y no-testeables.
 *
 * Reglas vigentes (capturadas del componente legado):
 *   - Eventos técnicos (`system_event`, `agent_tool_call`, `tool_execution_result`)
 *     no se muestran en el panel de chat — sólo en el panel derecho.
 *   - "Ghost triggers" son user_messages inyectados por la capa Temporal/LLM
 *     (`[SISTEMA]: ...`) — pertenecen al log técnico, no al diálogo humano.
 *   - "Agent echoes" son confirmaciones internas tras llamar a tools
 *     (`ha sido etiquetada`, `remarketing automático`) — ruido para el cliente.
 */

import type { ChatMessage, MessageSender } from "./model";

const TECHNICAL_UI_TYPES = new Set<ChatMessage["ui_type"]>([
  "system_event",
  "agent_tool_call",
  "tool_execution_result",
]);

const GHOST_TRIGGER_MARKER = "[SISTEMA]:";
const AGENT_ECHO_MARKERS = ["ha sido etiquetada", "remarketing automático"];

export function isTechnicalEvent(msg: ChatMessage): boolean {
  return TECHNICAL_UI_TYPES.has(msg.ui_type);
}

export function isGhostTrigger(msg: ChatMessage): boolean {
  return (
    msg.ui_type === "user_message" &&
    typeof msg.content === "string" &&
    msg.content.includes(GHOST_TRIGGER_MARKER)
  );
}

export function isAgentEcho(msg: ChatMessage): boolean {
  return (
    msg.ui_type === "agent_message" &&
    typeof msg.content === "string" &&
    AGENT_ECHO_MARKERS.some((marker) => msg.content!.includes(marker))
  );
}

/** Mensajes visibles en el panel central de chat (cliente↔agente). */
export function isVisibleChatMessage(msg: ChatMessage): boolean {
  if (isTechnicalEvent(msg)) return false;
  if (isGhostTrigger(msg)) return false;
  if (isAgentEcho(msg)) return false;
  return true;
}

/** Derives the visual sender from ui_type.
 * Assumes isVisibleChatMessage was applied upstream.
 *
 * - "user_message"  → "user" (cliente)
 * - "human_message" → "human" (operador humano via dashboard handoff)
 * - cualquier otro  → "agent" (bot)
 */
export function getMessageSender(msg: ChatMessage): MessageSender {
  if (msg.ui_type === "user_message") return "user";
  if (msg.ui_type === "human_message") return "human";
  return "agent";
}
