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
  /** Solo con reason "dado_de_baja": registro de la baja (null = sin detalle). */
  optedOutAtMs?: number | null;
  optedOutSource?: string | null;
  optedOutCampaignId?: string | null;
  optedOutCampaignName?: string | null;
}

export interface CampaignAudience {
  recipients: AudienceRecipient[];
  /** Contactos que NO reciben esta campaña (humano, baja o campaña <48h). */
  skipped: SkippedContact[];
  /** Cuántos de los skipped se dieron de baja — ya no se les puede enviar. */
  optedOutCount: number;
  total: number;
}

export const SKIP_DADO_DE_BAJA = "dado_de_baja";

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
  if (key === "manual" || key === "importados") return "violet";
  return "neutral";
}

const SKIP_REASON_LABELS: Record<string, string> = {
  excluido: "Atendido por humano",
  dado_de_baja: "Se dio de baja — ya no se le puede enviar",
  campana_reciente: "Campaña reciente (<48h)",
  quitado_por_operador: "Quitado por vos",
};

const OPT_OUT_SOURCE_LABELS: Record<string, string> = {
  texto: "respondió pidiendo la baja",
  meta: "se dio de baja desde WhatsApp",
};

/** Por qué vía se dio de baja el contacto; "" si la baja es vieja (sin
 *  detalle) o la vía es desconocida. */
export function optOutSourceLabel(source: string | null | undefined): string {
  if (!source) return "";
  return OPT_OUT_SOURCE_LABELS[source] ?? source;
}

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
