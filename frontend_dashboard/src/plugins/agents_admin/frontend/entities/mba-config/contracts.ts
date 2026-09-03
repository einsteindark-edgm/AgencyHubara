/**
 * Contratos Zod de la entidad `mba-config`: la configuración de Meta Business
 * Agent normalizada desde el workspace de un agente, tal como la devuelve
 * `GET /api/agents/{agent_id}/mba-config`. Los nombres de campo espejan los
 * endpoints `/agent_config/*` de Meta (skills, business_info, faq, settings).
 */
import { z } from "zod";

export const mbaSkillSchema = z.object({
  title: z.string(),
  description: z.string(),
  skill: z.string(),
  char_count: z.number(),
  char_limit: z.number(),
  over_limit: z.boolean(),
  sources: z.array(z.string()),
});

export const mbaFaqSchema = z.object({
  question: z.string(),
  answer: z.string(),
  source: z.string(),
});

export const mbaContactInfoSchema = z.object({
  email: z.string().nullable(),
  hours_of_operation: z.string().nullable(),
  address: z.string().nullable(),
});

export const mbaBusinessInfoSchema = z.object({
  business_description: z.string(),
  payment_method: z.string(),
  delivery_and_shipping: z.string(),
  return_policy: z.string(),
  purchase_info: z.string(),
  contact_info: mbaContactInfoSchema,
  sources: z.array(z.string()),
});

export const mbaPhraseSchema = z.object({
  phrase: z.string(),
  source: z.string(),
});

export const mbaSettingsSchema = z.object({
  rollout_enabled: z.boolean(),
  ai_audience: z.enum(["EVERYONE", "ALLOWLISTED_ONLY"]),
  handoff: z.object({
    enabled: z.boolean(),
    message: z.string().nullable(),
    message_selection: z.enum(["DEFAULT", "AGENT", "CUSTOM"]),
  }),
  followup: z.object({
    enabled: z.boolean(),
    followup_interval_in_seconds: z.number(),
    message: z.string().nullable(),
  }),
  never_say_phrases: z.array(mbaPhraseSchema),
});

export const mbaExcludedSchema = z.object({
  source: z.string(),
  reason: z.string(),
});

export const mbaEndpointSchema = z.object({
  section: z.string(),
  method: z.string(),
  path: z.string(),
});

export const mbaConnectorToolSchema = z.object({
  name: z.string(),
  description: z.string(),
  method: z.string(),
  path: z.string(),
  query_parameters: z.array(z.string()),
  body_parameters: z.array(z.string()),
  bindings: z.array(z.string()),
  write: z.boolean(),
  notes: z.string(),
  source: z.string(),
});

export const mbaConnectorSchema = z.object({
  name: z.string(),
  description: z.string(),
  base_url: z.string(),
  auth_type: z.enum(["OAUTH2_CLIENT_CREDENTIALS", "API_KEY", "NONE"]),
  auth_header: z.string(),
  requires_certificate: z.boolean(),
  tools: z.array(mbaConnectorToolSchema),
});

export const mbaUiSkillSchema = z.object({
  title: z.string(),
  component_type: z.string(),
  status: z.string(),
  instruction: z.string(),
  from_tool: z.string(),
  source: z.string(),
});

export const mbaToolTreatmentSchema = z.object({
  llm_tool: z.string(),
  when: z.string(),
  treatment: z.enum(["connector_tool", "ui_skill", "native_handoff", "internal", "unmapped"]),
  detail: z.string(),
});

export const mbaConfigSchema = z.object({
  agent_id: z.string(),
  channel: z.string(),
  business_info: mbaBusinessInfoSchema,
  settings: mbaSettingsSchema,
  skills: z.array(mbaSkillSchema),
  faqs: z.array(mbaFaqSchema),
  connector: mbaConnectorSchema.nullable(),
  ui_skills: z.array(mbaUiSkillSchema),
  tool_treatments: z.array(mbaToolTreatmentSchema),
  excluded: z.array(mbaExcludedSchema),
  endpoints: z.array(mbaEndpointSchema),
});

export type MbaConfigDto = z.infer<typeof mbaConfigSchema>;
export type MbaSkillDto = z.infer<typeof mbaSkillSchema>;
export type MbaBusinessInfoDto = z.infer<typeof mbaBusinessInfoSchema>;
