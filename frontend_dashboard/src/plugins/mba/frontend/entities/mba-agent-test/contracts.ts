/**
 * Contratos Zod de `mba-agent-test` (D2.4): un turno del simulador de Meta
 * (`POST /api/mba/agents/{id}/test`). Espeja el `agent_test` de la Cloud
 * API de MBA (v2.0.0) tal como lo devuelve el backend.
 */
import { z } from "zod";

export const mbaAgentTestReplySchema = z.object({
  message_id: z.string(),
  agent_response: z.string(),
  conversation_id: z.string(),
  // El OpenAPI de Meta marca estos campos `nullable`: aceptar null además de ausente.
  timestamp: z.number().nullish(),
  handoff_reason: z.string().nullish(),
  no_response_reason: z.string().nullish(),
  quick_replies: z.array(z.string()).nullish(),
  product_variant_ids: z.array(z.string()).nullish(),
});

export const mbaAgentTestOutcomeSchema = z.object({
  ok: z.boolean(),
  reply: mbaAgentTestReplySchema.nullable(),
  error: z.object({ kind: z.string(), status: z.number().nullable(), detail: z.string() }).nullable(),
});

export type MbaAgentTestReply = z.infer<typeof mbaAgentTestReplySchema>;
export type MbaAgentTestOutcome = z.infer<typeof mbaAgentTestOutcomeSchema>;
