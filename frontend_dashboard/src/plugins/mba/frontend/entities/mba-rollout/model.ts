/**
 * Tipos de la entidad `mba-rollout` + vocabulario de UI (chequeos de
 * readiness y motivos de rechazo de la política, en castellano).
 */
import type { z } from "zod";
import type {
  mbaAudienceSchema,
  mbaRolloutCheckSchema,
  mbaRolloutEntrySchema,
  mbaRolloutOutcomeSchema,
  mbaRolloutStatusSchema,
} from "./contracts";

export type MbaAudience = z.infer<typeof mbaAudienceSchema>;
export type MbaRolloutCheck = z.infer<typeof mbaRolloutCheckSchema>;
export type MbaRolloutEntry = z.infer<typeof mbaRolloutEntrySchema>;
export type MbaRolloutStatus = z.infer<typeof mbaRolloutStatusSchema>;
export type MbaRolloutOutcome = z.infer<typeof mbaRolloutOutcomeSchema>;

export const CHECK_LABEL: Record<string, string> = {
  flag_enabled: "Flag MBA_STANDBY_ENABLED encendida en Hubara",
  sync_ok: "Último sync con Meta OK",
  connector_active: "Connector activo en Meta",
  audience_allowlisted_only: "Audiencia ALLOWLISTED_ONLY",
  allowlist_nonempty: "Allowlist con al menos un teléfono",
  allowlist_within_hubara: "Todos los teléfonos están en la lista cerrada de Hubara",
};

export const REASON_TEXT: Record<string, string> = {
  not_ready: "No está listo: faltan chequeos.",
  confirmation_required: "Falta la confirmación.",
  everyone_not_allowed: "EVERYONE deshabilitado por política (MBA_ALLOW_EVERYONE apagado).",
  customer_not_in_hubara_allowlist: "Ese teléfono no está en la lista cerrada de Hubara (MBA_CUSTOMER_ALLOWLIST): agregarlo ahí primero.",
  already_listed: "Ese teléfono ya está en la allowlist de Meta.",
  invalid_phone: "Teléfono inválido: usá E.164 (+573001234567).",
  mba_disabled: "MBA está apagado en Hubara (MBA_STANDBY_ENABLED).",
  entity_id_missing: "El agente no tiene entity_id: onboardear el número primero.",
  rejected: "Meta rechazó la operación.",
};

export function describeReason(outcome: MbaRolloutOutcome): string {
  const base = REASON_TEXT[outcome.reason] ?? outcome.reason;
  const detail = outcome.error?.detail ? ` ${outcome.error.detail}` : "";
  return `${base}${detail}`;
}

export function pendingChecks(status: MbaRolloutStatus): MbaRolloutCheck[] {
  return status.checks.filter((c) => !c.ok);
}
