/**
 * Tipos de la entidad "agent" (agente IA configurable).
 * Cada agente tiene una personalidad (`profile`) que determina los 5 prompts
 * (Agents / Identity / Soul / Tools / Users) que se muestran en lectura.
 */

import type { IconName } from "@/shared/ui";

export type AgentColor = "blue" | "purple" | "green" | "orange" | "pink" | "teal";
export type AgentStatus = "online" | "idle" | "off";
export type PersonalityKey = "prof" | "friend" | "direct" | "empath";

export interface Capability {
  name: string;
  icon: IconName;
}

export interface Agent {
  id: string;
  name: string;
  role: string;
  model: string;
  icon: IconName;
  color: AgentColor;
  status: AgentStatus;
  calls: number;
  csat: number | null;
  personality: PersonalityKey;
  category: string;
  capabilities: Capability[];
}

export interface PersonalityPrompt {
  agents: string;
  identity: string;
  soul: string;
  tools: string;
  users: string;
}

export interface Personality {
  key: PersonalityKey;
  name: string;
  icon: IconName;
  desc: string;
  tags: string[];
  prompts: PersonalityPrompt;
}

export interface PromptSection {
  key: keyof PersonalityPrompt;
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
