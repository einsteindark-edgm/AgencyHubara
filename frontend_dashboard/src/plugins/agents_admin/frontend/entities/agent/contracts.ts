/**
 * Contratos Zod de la entidad `agent`. Única fuente de verdad del shape que
 * devuelve `GET /api/agents`. Los tipos de `model.ts` se derivan de aquí
 * (`z.infer`) para no duplicar la forma.
 */
import { z } from "zod";

export const agentColorSchema = z.enum([
  "blue",
  "purple",
  "green",
  "orange",
  "pink",
  "teal",
]);

/** Claves de las 5 secciones de prompt (espejan los archivos del workspace). */
export const promptKeySchema = z.enum([
  "agents",
  "identity",
  "soul",
  "tools",
  "users",
]);

export const agentCapabilitySchema = z.object({
  label: z.string(),
  icon: z.string(),
});

/** Contenido REAL de un archivo del workspace del agente. */
export const agentPromptSchema = z.object({
  key: promptKeySchema,
  filename: z.string(),
  content: z.string(),
  word_count: z.number(),
});

export const agentSchema = z.object({
  id: z.string(),
  name: z.string(),
  role: z.string(),
  model: z.string().nullable(),
  category: z.string(),
  icon: z.string(),
  color: agentColorSchema,
  workspace: z.string(),
  capabilities: z.array(agentCapabilitySchema),
  prompts: z.array(agentPromptSchema),
});

export const agentsListResponseSchema = z.object({
  agents: z.array(agentSchema),
});

export type AgentDto = z.infer<typeof agentSchema>;
export type AgentCapabilityDto = z.infer<typeof agentCapabilitySchema>;
export type AgentPromptDto = z.infer<typeof agentPromptSchema>;
