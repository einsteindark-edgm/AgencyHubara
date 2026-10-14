/**
 * Schemas Zod de `/api/reviews/*` — el boundary HTTP de la sección Reseñas.
 * Espejo del JSON del router `src.plugins.reviews.api` (snake_case). Se
 * activan en `api.ts` con `.parse(raw)` y los mappers los pasan a camelCase.
 *
 * Tolerancia (L-10): un valor fuera del vocabulario (estado, modo) o una llave
 * que falte NO tumba la respuesta entera — la página nunca queda vacía en
 * silencio por un campo nuevo del backend.
 *
 * Errores (no son 2xx; se leen del `ApiError.body`): `{detail: "texto para el
 * operador"}` — p. ej. el 502 de «Publicar» cuando la tienda (Medusa) falla.
 */

import { z } from "zod";

const count = z.number().int().nonnegative().catch(0);

export const backendReviewProductSchema = z.object({
  product_id: z.string(),
  title: z.string().default(""),
  handle: z.string().nullable().default(null),
});

export const backendReviewCouponSchema = z.object({
  code: z.string(),
  percent: z.number(),
  /** Fin de la vigencia (epoch ms); null = sin vencimiento. */
  expires_at_ms: z.number().nullable().default(null),
});

export const backendReviewPublishedSchema = z.object({
  at_ms: z.number().nullable().default(null),
  /** Texto para el operador cuando la tienda no aceptó la reseña. */
  error: z.string().nullable().default(null),
});

export const backendReviewSchema = z.object({
  review_id: z.string(),
  order_id: z.string(),
  display_id: z.string().nullable().default(null),
  session_id: z.string().nullable().default(null),
  rating: z.number().int().min(1).max(5),
  comment: z.string().nullable().default(null),
  publish_consent: z.boolean().default(false),
  author_name: z.string().nullable().default(null),
  author_city: z.string().nullable().default(null),
  products: z.array(backendReviewProductSchema).default([]),
  photo_urls: z.array(z.string()).default([]),
  submitted_at_ms: z.number(),
  /** pending | approved | hidden — el mapper cae a pending si llega otro. */
  status: z.string().default("pending"),
  coupon: backendReviewCouponSchema.nullable().default(null),
  reply: z.string().nullable().default(null),
  team_alerted: z.boolean().default(false),
  published: backendReviewPublishedSchema.nullable().default(null),
});

export type BackendReview = z.infer<typeof backendReviewSchema>;

export const backendReviewCountsSchema = z.object({
  pending: count.default(0),
  approved: count.default(0),
  hidden: count.default(0),
});

/** GET /api/reviews/reviews?status=… (de la más nueva a la más vieja). */
export const backendReviewListSchema = z.object({
  reviews: z.array(backendReviewSchema).default([]),
  counts: backendReviewCountsSchema.default({ pending: 0, approved: 0, hidden: 0 }),
});

export type BackendReviewList = z.infer<typeof backendReviewListSchema>;

/** POST approve / hide / reply → `{review}`. */
export const backendReviewEnvelopeSchema = z.object({
  review: backendReviewSchema,
});

export const backendSummarySchema = z.object({
  requests: z.object({
    /** Formulario dentro de la ventana de 24 h. */
    sent_flow: count.default(0),
    /** Plantilla de marketing (fuera de la ventana). */
    sent_template: count.default(0),
    failed: count.default(0),
    skipped: count.default(0),
    total: count.default(0),
  }),
  reviews: z.object({
    total: count.default(0),
    average_rating: z.number().nullable().default(null),
    /** Llaves "1".."5" → cuántas reseñas con esas estrellas. */
    distribution: z.record(z.string(), z.number()).default({}),
    with_photo: count.default(0),
    low_rating: count.default(0),
  }),
  coupons: z.object({
    granted: count.default(0),
    used: count.default(0),
  }),
  settings: z.object({
    mode: z.enum(["off", "test", "on"]).catch("off"),
    google_url: z.string().nullable().default(null),
  }),
});

export type BackendSummary = z.infer<typeof backendSummarySchema>;
