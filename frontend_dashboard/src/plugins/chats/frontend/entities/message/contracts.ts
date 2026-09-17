/**
 * Zod schemas de validación en el boundary HTTP. Si el backend cambia un
 * `ui_type` o el shape de `content`, el parse explota acá en vez de
 * propagar `undefined` hasta una bubble que renderiza vacía.
 */

import { z } from "zod";

export const messageUiTypeSchema = z.enum([
  "user_message",
  "agent_message",
  "human_message",
  "system_event",
  "tool_execution_result",
  "agent_tool_call",
  /** Marker de envío no-textual del bot (catálogo, flow, botones, galería…)
   *  escrito por el flush de ui_intents del backend. Se pinta como nota de
   *  sistema en el panel central para que el operador pueda seguir la
   *  conversación. */
  "ui_component_sent",
]);

/**
 * Forma REAL del mensaje detrás del marker del historial, proyectada por el
 * backend (`chats/shared/chat_events.py`). Ausente = mensaje normal.
 *
 * Existe para que el panel pinte cada evento como lo que fue (botones que son
 * botones, foto que es foto, caption separado de la descripción de la IA) sin
 * que la UI tenga que parsear texto: el formato de los markers lo define el
 * backend y cambia con él.
 */
export const chatEventSchema = z.discriminatedUnion("kind", [
  z.object({
    kind: z.literal("bot_buttons"),
    body: z.string().nullable(),
    buttons: z.array(
      z.object({ title: z.string(), touched: z.boolean().optional() }),
    ),
  }),
  z.object({ kind: z.literal("button_tap"), title: z.string() }),
  z.object({
    kind: z.literal("customer_photo"),
    /** Lo que describió la IA. null si la visión falló — no se inventa. */
    vision: z.string().nullable(),
    /** Lo único que escribió la persona, o null. */
    caption: z.string().nullable(),
    receipt: z.boolean(),
  }),
  z.object({
    kind: z.literal("reaction"),
    /** null en historial viejo: el emoji se perdía al traducir. */
    emoji: z.string().nullable(),
    author: z.enum(["user", "bot"]),
  }),
]);

export const chatMessageSchema = z.object({
  ui_type: messageUiTypeSchema,
  role: z.string(),
  content: z.string().nullable(),
  tool_calls: z.array(z.unknown()).optional(),
  /** Quién escribió un turno `assistant` que no fue el bot: "human" =
   *  operador via dashboard handoff; "mba" = eco de Meta Business Agent
   *  (webhook standby, D1.4). Los turnos del bot no llevan este campo. */
  sender: z.enum(["human", "mba"]).optional(),
  timestamp: z.union([z.string(), z.number()]).optional(),
  name: z.string().optional(),
  /** Ref relativa a una imagen inbound que el cliente envió por WhatsApp
   *  (típicamente un comprobante de pago). El backend persiste la imagen y la
   *  expone en `/api/dashboard/media/...`; el frontend la pinta en la burbuja
   *  para que el operador humano la vea. Ausente en mensajes sin imagen. */
  image_url: z.string().optional(),
  /** Documento PDF adjunto (comprobante de pago típico) — inbound del cliente
   *  u outbound del operador. Ref servible + nombre visible para el chip
   *  clickeable de la burbuja. Ausentes en mensajes sin documento. */
  document_url: z.string().optional(),
  document_filename: z.string().optional(),
  /** id de Meta del mensaje (destino de las citas del cliente). */
  wamid: z.string().optional(),
  /** Forma real del mensaje (ver `chatEventSchema`). Ausente = normal. */
  event: chatEventSchema.optional(),
  /** El cliente respondió CITANDO un mensaje. `author`/`text`/`image_url`
   *  vienen cuando el backend pudo resolver la cita; solo `id` si no. */
  reply_to: z
    .object({
      id: z.string(),
      author: z.string().optional(),
      text: z.string().optional(),
      image_url: z.string().optional(),
    })
    .optional(),
});

export type ChatMessageDto = z.infer<typeof chatMessageSchema>;
