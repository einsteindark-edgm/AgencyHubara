/**
 * Tipos del dominio "mensaje". Espejo de los eventos JSONL que escupe
 * `src/dashboard/api.py:get_session_history` (campo `ui_type` se inyecta server-side).
 */

export type MessageUiType =
  | "user_message"
  | "agent_message"
  | "system_event"
  | "tool_execution_result"
  | "agent_tool_call";

export interface ChatMessage {
  ui_type: MessageUiType;
  role: string;
  content: string | null;
  tool_calls?: unknown[];
  /** ms epoch o ISO; el backend hoy no garantiza presencia */
  timestamp?: string | number;
  /** Para tool_execution_result */
  name?: string;
}
