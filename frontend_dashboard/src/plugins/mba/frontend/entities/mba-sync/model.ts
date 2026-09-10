/**
 * Tipos de la entidad `mba-sync` + vocabulario de UI para las acciones del
 * plan (lo que el operador lee antes de confirmar el envío a Meta).
 */
import type { z } from "zod";
import type {
  mbaSyncActionSchema,
  mbaSyncOpSchema,
  mbaSyncOutcomeSchema,
  mbaSyncPlanSchema,
  mbaSyncResultSchema,
  mbaSyncStateSchema,
} from "./contracts";

export type MbaSyncAction = z.infer<typeof mbaSyncActionSchema>;
export type MbaSyncOp = z.infer<typeof mbaSyncOpSchema>;
export type MbaSyncPlan = z.infer<typeof mbaSyncPlanSchema>;
export type MbaSyncResult = z.infer<typeof mbaSyncResultSchema>;
export type MbaSyncState = z.infer<typeof mbaSyncStateSchema>;
export type MbaSyncOutcome = z.infer<typeof mbaSyncOutcomeSchema>;

/** Acciones que escriben en Meta (las demás son informativas). */
export const CHANGE_ACTIONS: readonly MbaSyncAction[] = ["create", "update", "replace", "delete"];

export const ACTION_LABEL: Record<MbaSyncAction, string> = {
  create: "crear",
  update: "actualizar",
  replace: "reemplazar",
  delete: "borrar",
  noop: "sin cambios",
  skip: "fuera de alcance",
};

export const SECTION_LABEL: Record<string, string> = {
  business_info: "Business info",
  faqs: "FAQ",
  skills: "Skill",
  connector: "Connector",
  connector_tools: "Tool",
  ui_skills: "UI skill",
  settings: "Settings",
  allowlist: "Allowlist",
};

export const BLOCKER_LABEL: Record<string, string> = {
  entity_id_missing: "El agente no tiene entity_id (onboardear el número primero)",
  connector_api_key_missing: "Falta HUBARA_MBA_API_KEY en el API",
};

export function describeBlocker(code: string): string {
  if (BLOCKER_LABEL[code]) return `${BLOCKER_LABEL[code]} (${code})`;
  if (code.startsWith("placeholder:")) return `Placeholder sin resolver en el workspace: ${code}`;
  if (code.startsWith("problem:")) return `Problema del preview: ${code}`;
  return code;
}

export function planChanges(plan: MbaSyncPlan): MbaSyncOp[] {
  return plan.ops.filter((op) => CHANGE_ACTIONS.includes(op.action));
}
