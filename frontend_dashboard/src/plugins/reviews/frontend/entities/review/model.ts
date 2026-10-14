/**
 * Modelo de dominio de las reseñas tras la entrega (camelCase).
 *
 * Una reseña la deja el cliente con el formulario de WhatsApp después de
 * recibir su pedido. El equipo la modera: «Publicar» la manda a la tienda web
 * (Medusa), «Ocultar» la saca de la vista pública. Sin permiso del cliente
 * para publicarla, solo cuenta en el promedio.
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

export interface ReviewsSummary {
  requests: {
    /** Formulario dentro de la ventana de 24 h. */
    sentFlow: number;
    /** Plantilla de marketing (fuera de la ventana). */
    sentTemplate: number;
    failed: number;
    skipped: number;
    total: number;
  };
  reviews: {
    total: number;
    averageRating: number | null;
    /** De 5 a 1 estrellas, siempre las cinco filas. */
    distribution: Array<{ stars: StarCount; count: number }>;
    withPhoto: number;
    lowRating: number;
  };
  coupons: { granted: number; used: number };
  settings: { mode: ReviewsMode; googleUrl: string | null };
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
