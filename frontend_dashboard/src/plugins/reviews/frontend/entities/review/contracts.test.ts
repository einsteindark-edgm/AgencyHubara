/**
 * Contrato Zod de `/api/reviews/*` — fixtures con el JSON REAL del backend
 * (`GET /reviews`, `GET /summary`, y el `{review}` de approve/hide/reply).
 * Primera línea de defensa contra contract drift: si el backend cambia la
 * forma, esto truena acá y no como una página vacía en silencio (L-10).
 */
import { describe, expect, it } from "vitest";

import {
  mapBackendReview,
  mapBackendReviewList,
  mapBackendSummary,
} from "./api";
import {
  backendReviewEnvelopeSchema,
  backendReviewListSchema,
  backendSummarySchema,
} from "./contracts";
import {
  BARE_REVIEW_FIXTURE,
  LEGACY_SUMMARY_FIXTURE,
  REVIEW_FIXTURE,
  SUMMARY_FIXTURE,
} from "./fixtures";

describe("backendReviewListSchema (GET /api/reviews/reviews)", () => {
  it("parsea la lista real y la pasa a camelCase", () => {
    const list = mapBackendReviewList(
      backendReviewListSchema.parse({
        reviews: [REVIEW_FIXTURE, BARE_REVIEW_FIXTURE],
        counts: { pending: 3, approved: 10, hidden: 1 },
      }),
    );

    expect(list.counts).toEqual({ pending: 3, approved: 10, hidden: 1 });
    expect(list.reviews[0]).toEqual({
      reviewId: "rev_order_01J",
      orderId: "order_01J",
      displayId: "#31",
      sessionId: "wa_573001234567",
      rating: 5,
      comment: "Hermosa",
      publishConsent: true,
      authorName: "Ana G.",
      authorCity: "Bogotá",
      products: [{ productId: "prod_1", title: "Velón Encanto Silvestre", handle: "encanto" }],
      photoUrls: ["/api/reviews/photos/order_01J_0.jpg"],
      submittedAtMs: 1791000000000,
      status: "pending",
      coupon: { code: "GRACIAS10", percent: 10, expiresAtMs: 1796184000000 },
      reply: "¡Gracias!",
      teamAlerted: false,
      published: { atMs: 1791000100000, error: null },
    });
  });

  it("acepta los nulos reales: sin comentario, ciudad, cupón, respuesta ni publicación", () => {
    const [, bare] = mapBackendReviewList(
      backendReviewListSchema.parse({
        reviews: [REVIEW_FIXTURE, BARE_REVIEW_FIXTURE],
        counts: { pending: 0, approved: 0, hidden: 2 },
      }),
    ).reviews;

    expect(bare).toMatchObject({
      comment: null,
      authorCity: null,
      coupon: null,
      reply: null,
      published: null,
      publishConsent: false,
      teamAlerted: true,
      status: "hidden",
      photoUrls: [],
    });
  });

  it("una lista sin `counts` no tumba la página: cuenta en cero", () => {
    const list = mapBackendReviewList(backendReviewListSchema.parse({ reviews: [] }));
    expect(list).toEqual({ reviews: [], counts: { pending: 0, approved: 0, hidden: 0 } });
  });

  it("un estado desconocido no tumba la lista entera (L-10): queda por revisar", () => {
    const list = mapBackendReviewList(
      backendReviewListSchema.parse({ reviews: [{ ...REVIEW_FIXTURE, status: "raro" }] }),
    );
    expect(list.reviews[0]?.status).toBe("pending");
  });

  it("el error de publicación en la tienda llega al dominio", () => {
    const list = mapBackendReviewList(
      backendReviewListSchema.parse({
        reviews: [
          { ...REVIEW_FIXTURE, published: { at_ms: null, error: "La tienda no respondió." } },
        ],
      }),
    );
    expect(list.reviews[0]?.published).toEqual({ atMs: null, error: "La tienda no respondió." });
  });
});

describe("backendReviewEnvelopeSchema (approve / hide / reply)", () => {
  it("parsea `{review}` con la misma forma de la lista", () => {
    const review = mapBackendReview(
      backendReviewEnvelopeSchema.parse({ review: { ...REVIEW_FIXTURE, status: "approved" } })
        .review,
    );
    expect(review.reviewId).toBe("rev_order_01J");
    expect(review.status).toBe("approved");
  });
});

describe("backendSummarySchema (GET /api/reviews/summary)", () => {
  it("parsea el resumen real: invitaciones, promedio, reparto, cupones y modo", () => {
    const summary = mapBackendSummary(backendSummarySchema.parse(SUMMARY_FIXTURE));

    expect(summary).toEqual({
      requests: { sentFlow: 12, sentTemplate: 30, failed: 1, skipped: 4, unconfirmed: 0, total: 47 },
      // De más a menos pedidos; los motivos quedan tal cual (los traduce la vista).
      notInvited: [
        { reason: "expired", count: 4 },
        { reason: "disabled", count: 3 },
        { reason: "not_in_test_list", count: 2 },
        { reason: "opted_out", count: 1 },
      ],
      waiting: [
        { reason: "waiting_for_window", count: 2 },
        { reason: "quiet_hours", count: 1 },
      ],
      reviews: {
        total: 20,
        averageRating: 4.6,
        distribution: [
          { stars: 5, count: 15 },
          { stars: 4, count: 3 },
          { stars: 3, count: 1 },
          { stars: 2, count: 1 },
          { stars: 1, count: 0 },
        ],
        withPhoto: 6,
        lowRating: 2,
      },
      coupons: { granted: 20, used: 5 },
      settings: {
        mode: "test",
        googleUrl: "https://hubara.com.co/opinar/",
        flowConfigured: true,
        couponsReady: true,
      },
      storefront: { configured: true, lastOkAtMs: 1791000000000, lastError: null, dueAtMs: null },
    });
  });

  it("los avisos nuevos llegan al dominio: formulario, cupones, envíos cortados y la tienda", () => {
    const summary = mapBackendSummary(
      backendSummarySchema.parse({
        ...SUMMARY_FIXTURE,
        requests: { ...SUMMARY_FIXTURE.requests, unconfirmed: 2 },
        settings: { ...SUMMARY_FIXTURE.settings, flow_configured: false, coupons_ready: false },
        storefront: {
          configured: false,
          last_ok_at_ms: null,
          last_error: "HTTP 500",
          due_at_ms: 1791003600000,
        },
      }),
    );

    expect(summary.requests.unconfirmed).toBe(2);
    expect(summary.settings).toMatchObject({ flowConfigured: false, couponsReady: false });
    expect(summary.storefront).toEqual({
      configured: false,
      lastOkAtMs: null,
      lastError: "HTTP 500",
      dueAtMs: 1791003600000,
    });
  });

  it("un backend anterior (sin las llaves nuevas) no tumba la página ni inventa alarmas", () => {
    const summary = mapBackendSummary(backendSummarySchema.parse(LEGACY_SUMMARY_FIXTURE));

    expect(summary.requests.unconfirmed).toBe(0);
    expect(summary.notInvited).toEqual([]);
    expect(summary.waiting).toEqual([]);
    expect(summary.settings).toMatchObject({ flowConfigured: true, couponsReady: true });
    expect(summary.storefront).toEqual({
      configured: true,
      lastOkAtMs: null,
      lastError: null,
      dueAtMs: null,
    });
  });

  it("un motivo desconocido se conserva; los ceros y los valores raros no salen", () => {
    const summary = mapBackendSummary(
      backendSummarySchema.parse({
        ...SUMMARY_FIXTURE,
        not_invited: { disabled: 0, motivo_nuevo: 3, roto: "x" },
        waiting: "no es un mapa",
        storefront: { configured: "sí" },
      }),
    );

    expect(summary.notInvited).toEqual([{ reason: "motivo_nuevo", count: 3 }]);
    expect(summary.waiting).toEqual([]);
    expect(summary.storefront.configured).toBe(true);
  });

  it("sin reseñas todavía: promedio null y reparto en cero aunque falten llaves", () => {
    const summary = mapBackendSummary(
      backendSummarySchema.parse({
        ...SUMMARY_FIXTURE,
        reviews: { total: 0, average_rating: null, distribution: {}, with_photo: 0, low_rating: 0 },
        settings: { mode: "off", google_url: "https://hubara.com.co/opinar/" },
      }),
    );
    expect(summary.reviews.averageRating).toBeNull();
    expect(summary.reviews.distribution.map((d) => d.count)).toEqual([0, 0, 0, 0, 0]);
    expect(summary.settings.mode).toBe("off");
  });

  it("los tres modos del envío se leen tal cual", () => {
    for (const mode of ["off", "test", "on"] as const) {
      const summary = mapBackendSummary(
        backendSummarySchema.parse({ ...SUMMARY_FIXTURE, settings: { mode, google_url: "" } }),
      );
      expect(summary.settings.mode).toBe(mode);
    }
  });
});
