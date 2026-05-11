/**
 * Zod schemas de validación en el boundary HTTP. Si el backend cambia un
 * `ui_type` o el shape de `content`, el parse explota acá en vez de
 * propagar `undefined` hasta una bubble que renderiza vacía.
 */

import { z } from "zod";

export const messageUiTypeSchema = z.enum([
  "user_message",
  "agent_message",
  "system_event",
  "tool_execution_result",
  "agent_tool_call",
]);

export const chatMessageSchema = z.object({
  ui_type: messageUiTypeSchema,
  role: z.string(),
  content: z.string().nullable(),
  tool_calls: z.array(z.unknown()).optional(),
  timestamp: z.union([z.string(), z.number()]).optional(),
  name: z.string().optional(),
});

export type ChatMessageDto = z.infer<typeof chatMessageSchema>;
