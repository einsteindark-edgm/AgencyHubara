/**
 * Modelo de dominio de la audiencia de campaña (camelCase) + helpers puros
 * de presentación (tono por segmento, razón de skip legible).
 */

export interface AudienceRecipient {
  sessionId: string;
  phone: string;
  customerName: string | null;
  segment: string;
}

export interface SkippedContact {
  sessionId: string;
  phone: string;
  reason: string;
}

export interface CampaignAudience {
  recipients: AudienceRecipient[];
  /** Contactos que NO reciben esta campaña (humano/opt-out o campaña <48h). */
  skipped: SkippedContact[];
  total: number;
}

export type ConversationRole = "user" | "assistant";

export interface ConversationMessage {
  role: ConversationRole;
  /** "text" | "template" | … — tolerante a kinds nuevos del backend. */
  kind: string;
  content: string;
  timestamp: string | null;
}

export interface AudienceConversation {
  sessionId: string;
  messages: ConversationMessage[];
}

/** Tono del chip por segmento — claves de TONE_CLS.
 *  clientes=ok, interesados=info, manual (curaduría)=violet,
 *  frios (y desconocidos)=neutral. */
export function segmentTone(key: string): "ok" | "info" | "neutral" | "violet" {
  if (key === "clientes") return "ok";
  if (key === "interesados") return "info";
  if (key === "manual") return "violet";
  return "neutral";
}

const SKIP_REASON_LABELS: Record<string, string> = {
  excluido: "Atendido por humano u opt-out",
  campana_reciente: "Campaña reciente (<48h)",
  quitado_por_operador: "Quitado por vos",
};

/** Razón de skip legible para el operador; una razón nueva del backend se
 *  muestra cruda antes que ocultarse. */
export function skippedReasonLabel(reason: string): string {
  return SKIP_REASON_LABELS[reason] ?? reason;
}

/** Teléfono crudo del operador → session_id del vault: quita espacios,
 *  guiones y paréntesis, CONSERVA el `+` del prefijo, y antepone `wa_`. */
export function phoneToSessionId(rawPhone: string): string {
  return `wa_${rawPhone.replace(/[\s\-()]/g, "")}`;
}
