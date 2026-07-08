/**
 * Tipos del dominio "mensaje". Espejo de los eventos JSONL que escupe
 * `src/dashboard/api.py:get_session_history` (campo `ui_type` se inyecta server-side).
 */

export type MessageUiType =
  | "user_message"
  | "agent_message"
  | "human_message"
  | "system_event"
  | "tool_execution_result"
  | "agent_tool_call"
  /** Envío no-textual del bot (catálogo, flow, botones…) — nota de sistema. */
  | "ui_component_sent";

/** "human" = operador humano via dashboard handoff (no es el bot ni el cliente). */
export type MessageSender = "user" | "agent" | "human";

export interface ChatMessage {
  ui_type: MessageUiType;
  role: string;
  content: string | null;
  tool_calls?: unknown[];
  /** Marker exclusivo de mensajes humanos. */
  sender?: "human";
  /** ms epoch o ISO; el backend hoy no garantiza presencia */
  timestamp?: string | number;
  /** Para tool_execution_result */
  name?: string;
  /** Ref relativa a una imagen inbound (comprobante de pago / foto del
   *  cliente) servida por `/api/dashboard/media/...`. Solo presente en
   *  mensajes del cliente que adjuntaron una imagen. */
  image_url?: string;
}
