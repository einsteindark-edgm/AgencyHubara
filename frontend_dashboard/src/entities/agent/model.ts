import type { IconName } from "@/shared/ui";

export type AgentColor = "blue" | "purple" | "green" | "orange" | "pink" | "teal";
export type AgentStatus = "online" | "idle" | "off";

export interface Capability {
  name: string;
  icon: IconName;
}

export interface SkillContent {
  name: string;
  content: string;
}

export interface WorkspaceContent {
  identity: string;
  soul: string;
  tools: string;
  agents: string;
  users: string;
  skills: SkillContent[];
}

export interface Agent {
  id: string;
  plugin_id: string;
  worker_name: string;
  name: string;
  role: string;
  workspace: WorkspaceContent;
  model: string;
  icon: IconName;
  color: AgentColor;
  status: AgentStatus;
  calls: number | null;
  csat: number | null;
  category: string;
  capabilities: Capability[];
}

export interface PromptSection {
  key: Exclude<keyof WorkspaceContent, "skills">;
  title: string;
  desc: string;
  icon: IconName;
}

export const PROMPT_SECTIONS: PromptSection[] = [
  { key: "agents",   title: "Agents",   desc: "Coordinación con otros agentes y handoffs",   icon: "workflow" },
  { key: "identity", title: "Identity", desc: "Nombre, rol y cómo se presenta",              icon: "user" },
  { key: "soul",     title: "Soul",     desc: "Valores, propósito y forma de relacionarse",  icon: "smile" },
  { key: "tools",    title: "Tools",    desc: "Herramientas disponibles y cuándo usarlas",   icon: "bolt" },
  { key: "users",    title: "Users",    desc: "Audiencia objetivo y suposiciones de contexto", icon: "tag" },
];
