/**
 * Modelo de dominio de la campaña de marketing directo por WhatsApp
 * (camelCase — el snake_case vive solo en `contracts.ts` + mappers).
 *
 * La lógica pura (línea de oferta, checklist, editabilidad) ESPEJA el dominio
 * backend (`src/plugins/marketing/domain/campaigns.py`) — si el backend
 * cambia una frase del template, este espejo cambia con él (el preview del
 * inspector muestra EXACTAMENTE lo que va a salir).
 */

export type CampaignStatus =
  | "draft"
  | "scheduled"
  | "sending"
  | "sent"
  | "failed";

export type CampaignGoal = "" | "discount_general" | "discount_product" | "launch";

export interface CampaignMessage {
  header: string;
  body: string;
  footer: string;
  cta: string;
}

export interface SkippedRecipient {
  sessionId: string;
  reason: string;
}

export interface CampaignSendResult {
  planned: number;
  sent: number;
  failed: string[];
  skipped: SkippedRecipient[];
  unitCostUsdMicros: number;
  spentUsdMicros: number;
}

export interface CampaignTestSend {
  phone: string;
  atMs: number;
  waMessageId: string | null;
}

export interface Campaign {
  id: string;
  name: string;
  status: CampaignStatus;
  goal: CampaignGoal;
  percent: number;
  couponCode: string;
  validUntil: string;
  productHandle: string | null;
  segments: string[];
  message: CampaignMessage;
  templateName: string;
  scheduleAtMs: number | null;
  createdAtMs: number;
  updatedAtMs: number;
  sentAtMs: number | null;
  sendResult: CampaignSendResult | null;
  testSends: CampaignTestSend[];
  /** Curaduría manual: sesiones quitadas a mano de la audiencia. */
  excludedSessionIds: string[];
  /** Curaduría manual: sesiones agregadas a mano fuera del segmento. */
  extraSessionIds: string[];
}

export interface CampaignStats {
  campaignId: string;
  status: CampaignStatus;
  planned: number | null;
  sent: number | null;
  failedCount: number;
  skippedCount: number;
  unitCostUsdMicros: number | null;
  spentUsdMicros: number | null;
  replied: number;
  attributedOrders: number;
  attributedRevenueCop: number;
}

/** Patch parcial del PUT — cada campo presente se envía, el resto no viaja. */
export interface CampaignPatch {
  name?: string;
  goal?: CampaignGoal;
  percent?: number;
  couponCode?: string;
  validUntil?: string;
  productHandle?: string | null;
  segments?: string[];
  message?: CampaignMessage;
  /** REPLACE completo de las listas de curaduría (no merge). */
  excludedSessionIds?: string[];
  extraSessionIds?: string[];
}

/** Response de POST /send — el workflow Temporal ya arrancó (o quedó
 *  programado con start_delay). */
export interface CampaignSendStarted {
  workflowId: string;
  runId: string;
  scheduled: boolean;
}

/* ── Metadata de presentación ────────────────────────────────────────────── */

export type StatusTone = "neutral" | "info" | "warn" | "ok" | "danger";

export const CAMPAIGN_STATUS_META: Record<
  CampaignStatus,
  { label: string; tone: StatusTone }
> = {
  draft: { label: "Borrador", tone: "neutral" },
  scheduled: { label: "Programada", tone: "info" },
  sending: { label: "Enviando", tone: "warn" },
  sent: { label: "Enviada", tone: "ok" },
  failed: { label: "Fallida", tone: "danger" },
};

export const CAMPAIGN_GOALS: {
  key: Exclude<CampaignGoal, "">;
  label: string;
  description: string;
}[] = [
  {
    key: "discount_general",
    label: "Descuento general",
    description: "Un % de descuento sobre toda la tienda",
  },
  {
    key: "discount_product",
    label: "Producto existente",
    description: "Promocionar un producto del catálogo",
  },
  {
    key: "launch",
    label: "Lanzamiento",
    description: "Anunciar una novedad sin descuento",
  },
];

/* ── Reglas puras (espejo del backend) ──────────────────────────────────── */

/** Espejo de `_EDITABLE_STATUSES` — PUT/send devuelven 409 fuera de esto. */
export function isCampaignEditable(status: CampaignStatus): boolean {
  return status === "draft" || status === "scheduled";
}

export function goalNeedsProduct(goal: CampaignGoal): boolean {
  return goal === "discount_product" || goal === "launch";
}

export function goalUsesDiscount(goal: CampaignGoal): boolean {
  return goal !== "" && goal !== "launch";
}

/** Texto fijo de opt-out del template MARKETING aprobado por Meta. */
export const OPT_OUT_LINE =
  'Si prefieres no recibir más promociones, respóndeme "NO MÁS" y te doy de baja.';

/**
 * Línea de oferta del template — espejo EXACTO de `_campaign_offer_line`:
 * cupón > porcentaje > invitación genérica. Siempre non-empty.
 */
export function campaignOfferLine(
  c: Pick<Campaign, "couponCode" | "percent" | "validUntil">,
): string {
  const coupon = c.couponCode.trim();
  const validUntil = c.validUntil.trim();
  if (coupon) {
    let line = `Usa el código ${coupon} al pagar`;
    if (validUntil) line += ` — válido hasta ${validUntil}`;
    return line + ".";
  }
  if (c.percent) {
    let line = `Aprovecha el ${c.percent}% de descuento`;
    if (validUntil) line += ` — válido hasta ${validUntil}`;
    return line + ". Escríbeme aquí y te muestro el catálogo.";
  }
  return "Escríbeme aquí y te cuento más.";
}

/* ── Checklist de validación (inspector + gates del builder) ────────────── */

export interface ChecklistItem {
  key: string;
  label: string;
  done: boolean;
  /** Los `required` espejan `_validate_ready_to_send` + reglas por goal. */
  required: boolean;
}

export function campaignChecklist(c: Campaign): ChecklistItem[] {
  const items: ChecklistItem[] = [
    { key: "goal", label: "Objetivo definido", done: c.goal !== "", required: true },
  ];
  if (goalNeedsProduct(c.goal)) {
    items.push({
      key: "product",
      label: "Producto elegido",
      done: c.productHandle !== null && c.productHandle !== "",
      required: true,
    });
  }
  if (goalUsesDiscount(c.goal)) {
    items.push({
      key: "discount",
      label: "Descuento definido",
      done: c.percent > 0,
      required: true,
    });
  }
  items.push(
    {
      key: "message",
      label: "Mensaje listo",
      done: c.message.body.trim() !== "",
      required: true,
    },
    {
      key: "audience",
      label: "Audiencia elegida",
      done: c.segments.length > 0,
      required: true,
    },
    {
      key: "coupon",
      label: "Cupón (opcional)",
      done: c.couponCode.trim() !== "",
      required: false,
    },
  );
  return items;
}

/** ¿Los requeridos del checklist están completos? (gate del botón Enviar —
 *  espejo del 422 de POST /send). */
export function campaignReadyToSend(c: Campaign): boolean {
  return campaignChecklist(c)
    .filter((i) => i.required)
    .every((i) => i.done);
}
