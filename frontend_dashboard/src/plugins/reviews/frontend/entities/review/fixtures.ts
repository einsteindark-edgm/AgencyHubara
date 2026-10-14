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

/** El resumen como lo manda hoy el backend: todo listo para invitar (sin
 *  alerta de «Qué falta») y la tienda web al día. */
export const SUMMARY_FIXTURE = {
  requests: { sent_flow: 12, sent_template: 30, failed: 1, skipped: 4, unconfirmed: 0, total: 47 },
  /** Por qué no se invitó a un pedido (motivo → cuántos pedidos). */
  not_invited: { disabled: 3, not_in_test_list: 2, opted_out: 1, expired: 4 },
  /** Qué espera un pedido para que salga su invitación (motivo → cuántos). */
  waiting: { quiet_hours: 1, waiting_for_window: 2 },
  reviews: {
    total: 20,
    average_rating: 4.6,
    distribution: { "1": 0, "2": 1, "3": 1, "4": 3, "5": 15 },
    with_photo: 6,
    low_rating: 2,
  },
  coupons: { granted: 20, used: 5 },
  settings: {
    mode: "test",
    google_url: "https://hubara.com.co/opinar/",
    flow_configured: true,
    coupons_ready: true,
  },
  storefront: { configured: true, last_ok_at_ms: 1791000000000, last_error: null, due_at_ms: null },
};

/** El resumen de un backend anterior: sin `not_invited`, `waiting`,
 *  `storefront`, `unconfirmed` ni los avisos de `settings`. */
export const LEGACY_SUMMARY_FIXTURE = {
  requests: { sent_flow: 12, sent_template: 30, failed: 1, skipped: 4, total: 47 },
  reviews: SUMMARY_FIXTURE.reviews,
  coupons: SUMMARY_FIXTURE.coupons,
  settings: { mode: "test", google_url: "https://hubara.com.co/opinar/" },
};
