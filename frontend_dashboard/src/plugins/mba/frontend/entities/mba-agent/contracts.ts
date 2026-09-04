/**
 * Contratos Zod de la entidad `mba-agent`: los agentes de Meta Business Agent
 * autorados en el plugin (uno por carpeta `agents/<id>/`), tal como los
 * devuelve `GET /api/mba/agents`.
 */
import { z } from "zod";

export const mbaAgentSchema = z.object({
  id: z.string(),
  display_name: z.string(),
  role: z.string(),
  channel: z.string(),
  icon: z.string(),
  color: z.string(),
  entity_id: z.string().nullable(),
});

export const mbaAgentsResponseSchema = z.object({
  agents: z.array(mbaAgentSchema),
});

export type MbaAgentDto = z.infer<typeof mbaAgentSchema>;
