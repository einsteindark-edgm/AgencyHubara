/**
 * Modelo de dominio de las reseñas tras la entrega (camelCase).
 *
 * Una reseña la deja el cliente con el formulario de WhatsApp después de
 * recibir su pedido. El equipo la modera: «Publicar» la manda a la tienda web
 * (Medusa), «Ocultar» la saca de la vista pública. Sin permiso del cliente
 * también se publica, pero anónima y solo con sus estrellas (sin comentario,
 * ciudad ni foto).
 */

import { BOGOTA_TZ } from "@/shared/lib";
import { ApiError } from "@/shared/sdk";

export type ReviewStatus = "pending" | "approved" | "hidden";

export interface ReviewProduct {
  productId: string;
  title: string;
  handle: string | null;
}

export interface ReviewCoupon {
  code: string;
  percent: number;
  /** Fin de la vigencia (epoch ms); null = sin vencimiento. */
  expiresAtMs: number | null;
}

export interface ReviewPublished {
  atMs: number | null;
  /** Lo que respondió la tienda cuando no aceptó la reseña. */
  error: string | null;
}

export interface Review {
  reviewId: string;
  orderId: string;
  /** "#31" — el número que ve el equipo en Pedidos. */
  displayId: string | null;
  sessionId: string | null;
  /** 1 a 5 estrellas. */
  rating: number;
  comment: string | null;
  /** El cliente dio permiso para publicarla en la tienda. */
  publishConsent: boolean;
  authorName: string | null;
  authorCity: string | null;
  products: ReviewProduct[];
  /** Refs relativas de nuestro API: pasar por `apiFileUrl` para `<img>`. */
  photoUrls: string[];
  submittedAtMs: number;
  status: ReviewStatus;
  coupon: ReviewCoupon | null;
  reply: string | null;
  /** Con 1–3 estrellas la conversación pasó al equipo. */
  teamAlerted: boolean;
  published: ReviewPublished | null;
}

export interface ReviewCounts {
  pending: number;
  approved: number;
  hidden: number;
}

export interface ReviewList {
  reviews: Review[];
  counts: ReviewCounts;
}

export type ReviewsMode = "off" | "test" | "on";

export type StarCount = 1 | 2 | 3 | 4 | 5;

/** Un motivo del flujo (`not_invited` / `waiting`) y cuántos pedidos tiene.
 *  `reason` queda tal cual lo manda el backend; la vista lo traduce. */
export interface ReasonCount {
  reason: string;
  count: number;
}

/** La reconstrucción de la tienda web, que es la que muestra las reseñas. */
export interface StorefrontStatus {
  /** false = falta STOREFRONT_DEPLOY_HOOK_URL. */
  configured: boolean;
  lastOkAtMs: number | null;
  lastError: string | null;
  /** Próxima reconstrucción pendiente (epoch ms). */
  dueAtMs: number | null;
}

export interface ReviewsSummary {
  requests: {
    /** Formulario dentro de la ventana de 24 h. */
    sentFlow: number;
    /** Plantilla de marketing (fuera de la ventana). */
    sentTemplate: number;
    failed: number;
    skipped: number;
    /** Envíos cortados a mitad: no se sabe si llegaron (no se reintentan). */
    unconfirmed: number;
    total: number;
  };
  /** Por qué no se invitó — de más a menos pedidos, sin ceros. */
  notInvited: ReasonCount[];
  /** Qué espera cada pedido para que salga su invitación — igual orden. */
  waiting: ReasonCount[];
  reviews: {
    total: number;
    averageRating: number | null;
    /** De 5 a 1 estrellas, siempre las cinco filas. */
    distribution: Array<{ stars: StarCount; count: number }>;
    withPhoto: number;
    lowRating: number;
  };
  coupons: { granted: number; used: number };
  settings: {
    mode: ReviewsMode;
    googleUrl: string | null;
    /** false = falta el formulario de Meta (META_FLOW_ID_REVIEW). */
    flowConfigured: boolean;
    /** false = faltan los cupones GRACIAS5/GRACIAS10. */
    couponsReady: boolean;
  };
  storefront: StorefrontStatus;
}


/* ── Helpers ────────────────────────────────────────────────────────────── */

/** Largo máximo de la respuesta de la tienda (mismo tope que el backend). */
export const REPLY_MAX_CHARS = 600;

export const REVIEW_TABS: ReadonlyArray<{ status: ReviewStatus; label: string }> = [
  { status: "pending", label: "Por revisar" },
  { status: "approved", label: "Publicadas" },
  { status: "hidden", label: "Ocultas" },
];

export type ReviewTone = "neutral" | "info" | "warn" | "ok" | "danger";

/** Qué pasa hoy con las invitaciones, dicho para el equipo. */
export const MODE_META: Record<ReviewsMode, { label: string; tone: ReviewTone }> = {
  off: {
    label: "Apagado: no se están enviando invitaciones (se enciende por Terraform)",
    tone: "neutral",
  },
  test: { label: "Solo números de prueba", tone: "info" },
  on: { label: "Activo", tone: "ok" },
};

/** Por qué no se invitó a un pedido, dicho para el equipo. Un motivo que no
 *  esté acá se muestra con su nombre tal cual (`reasonLabel`). */
export const NOT_INVITED_LABELS: Readonly<Record<string, string>> = {
  disabled: "El programa estaba apagado",
  not_in_test_list: "No era un número de prueba",
  order_not_eligible: "Pedido de prueba o ya no entregado",
  opted_out: "Se dio de baja de promociones",
  expired: "Pasaron 48 h sin poder escribirle",
  flow_not_configured: "Faltaba el formulario de Meta",
  max_steps: "Se agotaron los intentos",
};

/** Qué espera un pedido para que salga su invitación. */
export const WAITING_LABELS: Readonly<Record<string, string>> = {
  coupon_not_configured: "Faltan los cupones",
  quiet_hours: "Horario de silencio",
  waiting_for_window: "Esperando a que escriba (plantilla desde las 20 h)",
  min_delay: "Esperando 2 h tras la entrega",
  conversation_busy: "La conversación está ocupada",
  another_invitation_pending: "Ya tiene otra invitación sin contestar",
  order_unknown: "Medusa no respondió",
};

/** La etiqueta en español de un motivo, o el motivo tal cual si es nuevo. */
export function reasonLabel(labels: Readonly<Record<string, string>>, reason: string): string {
  return Object.hasOwn(labels, reason) ? (labels[reason] ?? reason) : reason;
}

/* Fechas SIEMPRE en hora de Colombia: el navegador del equipo puede estar en
 * otra zona y «cuándo» es el de la tienda. Los meses van escritos a mano para
 * que el texto no cambie con la versión de ICU del navegador ("dic" vs "dic."). */
const MONTHS_SHORT = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

const BOGOTA_PARTS = new Intl.DateTimeFormat("en-US", {
  timeZone: BOGOTA_TZ,
  year: "numeric",
  month: "numeric",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function bogotaParts(ms: number): { year: number; month: number; day: number; hour: string; minute: string } {
  const parts = Object.fromEntries(
    BOGOTA_PARTS.formatToParts(new Date(ms)).map((p) => [p.type, p.value]),
  );
  return {
    year: Number(parts.year),
    month: Number(parts.month),
    day: Number(parts.day),
    // Algunos ICU emiten "24" para la medianoche aun con h23.
    hour: parts.hour === "24" ? "00" : (parts.hour ?? "00"),
    minute: parts.minute ?? "00",
  };
}

/** "12 dic" — día y mes corto en Bogotá. */
export function formatShortDayBogota(ms: number): string {
  const p = bogotaParts(ms);
  return `${p.day} ${MONTHS_SHORT[p.month - 1]}`;
}

/** "GRACIAS10 · 10 % · vence 12 dic" — el cupón que recibió el cliente. */
export function couponChipLabel(c: ReviewCoupon): string {
  const base = `${c.code} · ${c.percent} %`;
  return c.expiresAtMs === null ? base : `${base} · vence ${formatShortDayBogota(c.expiresAtMs)}`;
}

/** "23:30" — hora y minutos en Bogotá (24 h). */
export function formatTimeBogota(ms: number): string {
  const p = bogotaParts(ms);
  return `${p.hour}:${p.minute}`;
}

/** "2 oct, 23:00" — con el año si no es el año en curso. `nowMs` se lee en
 *  render (regla 5): no llamarla desde un mapper ni un queryFn. */
export function formatReviewDate(ms: number, nowMs: number = Date.now()): string {
  const p = bogotaParts(ms);
  const sameYear = p.year === bogotaParts(nowMs).year;
  const day = `${p.day} ${MONTHS_SHORT[p.month - 1]}${sameYear ? "" : ` ${p.year}`}`;
  return `${day}, ${p.hour}:${p.minute}`;
}

const ONE_DECIMAL = new Intl.NumberFormat("es-CO", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

/** "4,6" — el promedio de estrellas con un decimal. */
export function formatAverage(n: number): string {
  return ONE_DECIMAL.format(n);
}

/** Mensaje para el equipo de un error de la API: el `detail` que escribió el
 *  backend (p. ej. el 502 de la tienda al publicar) o uno llano — nunca un
 *  "[object Object]" ni el "Failed to fetch" del navegador. */
export function reviewErrorDetail(err: unknown): string {
  if (err instanceof ApiError) {
    const body = err.body;
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail?: unknown }).detail;
      if (typeof detail === "string" && detail.trim()) return detail;
    }
    return `Algo falló en el servidor (error ${err.status}). Inténtalo de nuevo.`;
  }
  // `fetch` sin red lanza TypeError; lo demás (p. ej. Zod) es una respuesta
  // que llegó con otra forma.
  if (err instanceof TypeError) return "No hubo conexión con el servidor. Inténtalo de nuevo.";
  return "El servidor respondió algo que no se pudo leer. Inténtalo de nuevo.";
}
