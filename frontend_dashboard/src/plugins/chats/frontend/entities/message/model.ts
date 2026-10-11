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

/** Forma real del mensaje detrás del marker (ver `chatEventSchema`). */
export type ChatEvent =
  | {
      kind: "bot_buttons";
      body: string | null;
      buttons: { title: string; touched?: boolean }[];
    }
  | { kind: "button_tap"; title: string }
  | {
      kind: "customer_photo";
      vision: string | null;
      caption: string | null;
      receipt: boolean;
    }
  | { kind: "reaction"; emoji: string | null; author: "user" | "bot" }
  | ShippingFormEvent;

/** Respuesta del formulario (Flow) de envío. null = el campo no llegó. */
export interface ShippingFormEvent {
  kind: "shipping_form";
  receiver_name: string | null;
  phone: string | null;
  city: string | null;
  neighborhood: string | null;
  address: string | null;
  payment_method: string | null;
  order_total_cop: number | null;
  items_summary: string | null;
  /** Campos que el Flow agregó y el panel aún no conoce. */
  extra: { key: string; value: string }[];
}

/** "human" = operador humano via dashboard handoff (no es el bot ni el cliente). */
export type MessageSender = "user" | "agent" | "human";

export interface ChatMessage {
  ui_type: MessageUiType;
  role: string;
  content: string | null;
  tool_calls?: unknown[];
  /** "human" = operador (handoff); "mba" = eco de Meta Business Agent. */
  sender?: "human" | "mba";
  /** ms epoch o ISO; el backend hoy no garantiza presencia */
  timestamp?: string | number;
  /** Para tool_execution_result */
  name?: string;
  /** Ref relativa a una imagen inbound (comprobante de pago / foto del
   *  cliente) servida por `/api/dashboard/media/...`. Solo presente en
   *  mensajes del cliente que adjuntaron una imagen. */
  image_url?: string;
  /** Documento PDF adjunto (comprobante típico): ref servible + nombre
   *  visible. Aplica a inbound del cliente y outbound del operador. */
  document_url?: string;
  document_filename?: string;
  /** id de Meta del mensaje. */
  wamid?: string;
  /** Turno del bot que produjo el mensaje (plan del laboratorio PR 17). */
  turn_key?: string;
  /** Forma real del mensaje detrás del marker, o ausente. */
  event?: ChatEvent;
  /** Mensaje del cliente entregado tarde por Meta: cuándo lo escribió (ISO o
   *  epoch) y si llegó con la ventana de 24 h ya cerrada. */
  sent_at?: string | number;
  arrived_after_window?: boolean;
  /** Mensaje citado por el cliente (reply). Solo `id` si no se resolvió. */
  reply_to?: {
    id: string;
    author?: string;
    text?: string;
    image_url?: string;
  };
}
