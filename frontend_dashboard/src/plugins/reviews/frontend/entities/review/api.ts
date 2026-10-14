/**
 * Boundary HTTP de las reseñas: SOLO `/api/reviews/*`, con el `apiClient`
 * del SDK, `{signal}` en cada query y Zod `.parse()` antes de que el dato
 * siga. Los mappers pasan el snake_case del backend al dominio camelCase.
 *
 * Refresco: las tres acciones (publicar, ocultar, responder) invalidan TODO
 * lo de reseñas — la reseña cambia de bandeja, los conteos de las pestañas y
 * el resumen cambian. Publicar invalida también si falla: con el 502 de la
 * tienda el backend deja `published.error` en la reseña y la tarjeta lo
 * tiene que mostrar. Las reseñas nuevas llegan con el refetch de red de
 * seguridad (≥60 s, regla #2): el backend aún no publica un evento
 * `reviews` en el stream del dashboard.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/sdk";

import {
  backendReviewEnvelopeSchema,
  backendReviewListSchema,
  backendSummarySchema,
  type BackendReview,
  type BackendReviewList,
  type BackendSummary,
} from "./contracts";
import { reviewKeys } from "./keys";
import type {
  Review,
  ReviewList,
  ReviewStatus,
  ReviewsSummary,
  StarCount,
} from "./model";

/* ── Mappers backend → dominio ──────────────────────────────────────────── */

function asStatus(s: string): ReviewStatus {
  return s === "approved" || s === "hidden" ? s : "pending";
}

export function mapBackendReview(b: BackendReview): Review {
  return {
    reviewId: b.review_id,
    orderId: b.order_id,
    displayId: b.display_id,
    sessionId: b.session_id,
    rating: b.rating,
    comment: b.comment,
    publishConsent: b.publish_consent,
    authorName: b.author_name,
    authorCity: b.author_city,
    products: b.products.map((p) => ({
      productId: p.product_id,
      title: p.title,
      handle: p.handle,
    })),
    photoUrls: [...b.photo_urls],
    submittedAtMs: b.submitted_at_ms,
    status: asStatus(b.status),
    coupon: b.coupon
      ? { code: b.coupon.code, percent: b.coupon.percent, expiresAtMs: b.coupon.expires_at_ms }
      : null,
    reply: b.reply,
    teamAlerted: b.team_alerted,
    published: b.published ? { atMs: b.published.at_ms, error: b.published.error } : null,
  };
}

export function mapBackendReviewList(b: BackendReviewList): ReviewList {
  return {
    reviews: b.reviews.map(mapBackendReview),
    counts: { pending: b.counts.pending, approved: b.counts.approved, hidden: b.counts.hidden },
  };
}

const STARS_DESC: StarCount[] = [5, 4, 3, 2, 1];

export function mapBackendSummary(b: BackendSummary): ReviewsSummary {
  return {
    requests: {
      sentFlow: b.requests.sent_flow,
      sentTemplate: b.requests.sent_template,
      failed: b.requests.failed,
      skipped: b.requests.skipped,
      total: b.requests.total,
    },
    reviews: {
      total: b.reviews.total,
      averageRating: b.reviews.average_rating,
      distribution: STARS_DESC.map((stars) => ({
        stars,
        count: b.reviews.distribution[String(stars)] ?? 0,
      })),
      withPhoto: b.reviews.with_photo,
      lowRating: b.reviews.low_rating,
    },
    coupons: { granted: b.coupons.granted, used: b.coupons.used },
    settings: { mode: b.settings.mode, googleUrl: b.settings.google_url },
  };
}


/* ── Queries ────────────────────────────────────────────────────────────── */

const BASE = "/api/reviews";

/** Red de seguridad (regla #2): sin evento propio en el stream todavía. */
const FALLBACK_REFETCH_MS = 60_000;

const reviewPath = (reviewId: string) => `${BASE}/reviews/${encodeURIComponent(reviewId)}`;

/** Una bandeja (por revisar / publicadas / ocultas) + los conteos de las tres.
 *  Al cambiar de pestaña se conservan los datos anteriores (`isPlaceholderData`)
 *  para que los conteos no parpadeen; la lista los ignora mientras carga. */
export function useReviews(status: ReviewStatus) {
  return useQuery<ReviewList>({
    queryKey: reviewKeys.list(status),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>(
        `${BASE}/reviews?status=${encodeURIComponent(status)}`,
        { signal },
      );
      return mapBackendReviewList(backendReviewListSchema.parse(raw));
    },
    placeholderData: (previous) => previous,
    staleTime: 30_000,
    refetchInterval: FALLBACK_REFETCH_MS,
  });
}

/** Invitaciones, promedio, reparto de estrellas, cupones y modo del envío. */
export function useReviewsSummary() {
  return useQuery<ReviewsSummary>({
    queryKey: reviewKeys.summary(),
    queryFn: async ({ signal }) => {
      const raw = await apiClient.get<unknown>(`${BASE}/summary`, { signal });
      return mapBackendSummary(backendSummarySchema.parse(raw));
    },
    staleTime: 30_000,
    refetchInterval: FALLBACK_REFETCH_MS,
  });
}

/* ── Mutations ──────────────────────────────────────────────────────────── */

function useInvalidateReviews() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: reviewKeys.all });
}

/** POST /reviews/{id}/approve — la publica en la tienda. 502 `{detail}` si la
 *  tienda falla (la reseña sigue por revisar con `published.error`). */
export function useApproveReview(reviewId: string) {
  const invalidate = useInvalidateReviews();
  return useMutation<Review, Error, void>({
    mutationFn: async () => {
      const raw = await apiClient.post<unknown>(`${reviewPath(reviewId)}/approve`);
      return mapBackendReview(backendReviewEnvelopeSchema.parse(raw).review);
    },
    onSettled: invalidate,
  });
}

/** POST /reviews/{id}/hide — la saca de la vista pública. */
export function useHideReview(reviewId: string) {
  const invalidate = useInvalidateReviews();
  return useMutation<Review, Error, void>({
    mutationFn: async () => {
      const raw = await apiClient.post<unknown>(`${reviewPath(reviewId)}/hide`);
      return mapBackendReview(backendReviewEnvelopeSchema.parse(raw).review);
    },
    onSuccess: invalidate,
  });
}

/** POST /reviews/{id}/reply `{reply}` — la respuesta de la tienda (≤600). */
export function useReplyReview(reviewId: string) {
  const invalidate = useInvalidateReviews();
  return useMutation<Review, Error, string>({
    mutationFn: async (reply) => {
      const raw = await apiClient.post<unknown>(`${reviewPath(reviewId)}/reply`, { reply });
      return mapBackendReview(backendReviewEnvelopeSchema.parse(raw).review);
    },
    onSuccess: invalidate,
  });
}
