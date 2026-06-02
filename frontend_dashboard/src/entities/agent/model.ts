/**
 * Tipos de la entidad "agent" (un agente IA real del sistema).
 *
 * Cada agente trae sus 5 prompts (Agents / Identity / Soul / Tools / Users)
 * leídos del contenido REAL de los .md de su workspace — NO de personalidades
 * mockeadas. La forma se deriva de los contratos Zod (`contracts.ts`).
 */
import type { z } from "zod";
import type { IconName } from "@/shared/ui";
import type {
  agentSchema,
  agentCapabilitySchema,
  agentPromptSchema,
  promptKeySchema,
} from "./contracts";

export type AgentColor = "blue" | "purple" | "green" | "orange" | "pink" | "teal";
export type PromptKey = z.infer<typeof promptKeySchema>;

export type Agent = z.infer<typeof agentSchema>;
export type AgentCapability = z.infer<typeof agentCapabilitySchema>;
export type AgentPrompt = z.infer<typeof agentPromptSchema>;

/** Metadata de UI por sección de prompt (título, descripción, icono). */
export interface PromptSection {
  key: PromptKey;
  title: string;
  desc: string;
  icon: IconName;
}

export const PROMPT_SECTIONS: PromptSection[] = [
  { key: "agents",   title: "Agents",   desc: "Coordinación con otros agentes y handoffs",      icon: "workflow" },
  { key: "identity", title: "Identity", desc: "Nombre, rol y cómo se presenta",                 icon: "user" },
  { key: "soul",     title: "Soul",     desc: "Valores, propósito y forma de relacionarse",     icon: "smile" },
  { key: "tools",    title: "Tools",    desc: "Herramientas disponibles y cuándo usarlas",      icon: "bolt" },
  { key: "users",    title: "Users",    desc: "Audiencia objetivo y suposiciones de contexto",  icon: "tag" },
];
