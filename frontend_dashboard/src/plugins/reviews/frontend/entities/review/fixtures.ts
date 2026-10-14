/**
 * Fixtures de PRUEBA con el JSON REAL de `/api/reviews/*` (snake_case, tal
 * cual lo emite el backend). Los usan los tests de la entity, de las features
 * y de la Page — una sola fuente para que todos hablen del mismo contrato.
 * No las importa código de producción.
 */

export const REVIEW_FIXTURE = {
  review_id: "rev_order_01J",
  order_id: "order_01J",
  display_id: "#31",
  session_id: "wa_573001234567",
  rating: 5,
  comment: "Hermosa",
  publish_consent: true,
  author_name: "Ana G.",
  author_city: "Bogotá",
  products: [{ product_id: "prod_1", title: "Velón Encanto Silvestre", handle: "encanto" }],
  photo_urls: ["/api/reviews/photos/order_01J_0.jpg"],
  submitted_at_ms: 1791000000000,
  status: "pending",
  coupon: { code: "GRACIAS10", percent: 10, expires_at_ms: 1796184000000 },
  reply: "¡Gracias!",
  team_alerted: false,
  published: { at_ms: 1791000100000, error: null },
};

/** Reseña con todos los nulos que el backend puede mandar. */
export const BARE_REVIEW_FIXTURE = {
  ...REVIEW_FIXTURE,
  review_id: "rev_order_02K",
  order_id: "order_02K",
  display_id: "#32",
  rating: 2,
  comment: null,
  publish_consent: false,
  author_name: "Luis P.",
  author_city: null,
  photo_urls: [],
  status: "hidden",
  coupon: null,
  reply: null,
  team_alerted: true,
  published: null,
};

export const SUMMARY_FIXTURE = {
  requests: { sent_flow: 12, sent_template: 30, failed: 1, skipped: 4, total: 47 },
  reviews: {
    total: 20,
    average_rating: 4.6,
    distribution: { "1": 0, "2": 1, "3": 1, "4": 3, "5": 15 },
    with_photo: 6,
    low_rating: 2,
  },
  coupons: { granted: 20, used: 5 },
  settings: { mode: "test", google_url: "https://hubara.com.co/opinar/" },
};
