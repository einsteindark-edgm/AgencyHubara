import { z } from "zod";

export const skillContentSchema = z.object({
  name: z.string(),
  content: z.string(),
});

export const workspaceContentSchema = z.object({
  identity: z.string(),
  soul: z.string(),
  tools: z.string(),
  agents: z.string(),
  users: z.string(),
  skills: z.array(skillContentSchema).default([]),
});

export const agentDtoSchema = z.object({
  id: z.string(),
  plugin_id: z.string(),
  worker_name: z.string(),
  name: z.string(),
  role: z.string().default(""),
  workspace: workspaceContentSchema,
});

export const agentListDtoSchema = z.array(agentDtoSchema);
export type AgentDto = z.infer<typeof agentDtoSchema>;
