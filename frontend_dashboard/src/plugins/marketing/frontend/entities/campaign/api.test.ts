/**
 * Mappers del boundary campaña: snake_case (backend) → camelCase (dominio)
 * y el patch camelCase → body snake_case del PUT.
 */
import { describe, expect, it } from "vitest";

import { mapBackendCampaign, mapBackendStats, patchToBody } from "./api";
import type { BackendCampaign } from "./contracts";

const backendSent: BackendCampaign = {
  id: "mkt-f6g7h8i9j0",
  name: "Día del padre",
  status: "sent",
  goal: "discount_general",
  percent: 20,
  coupon_code: "PAPA20",
  valid_until: "15 de junio",
  product_handle: null,
  segments: ["clientes", "interesados"],
  message: { header: "H", body: "B", footer: "F", cta: "C" },
  template_name: "campaign_promo_marketing_v1",
  schedule_at_ms: null,
  created_at_ms: 1_784_600_000_000,
  updated_at_ms: 1_784_700_000_000,
  sent_at_ms: 1_784_700_000_000,
  send_result: {
    planned: 42,
    sent: 40,
    failed: ["wa_1"],
    skipped: [{ session_id: "wa_2", reason: "excluido" }],
    unit_cost_usd_micros: 12_500,
    spent_usd_micros: 500_000,
  },
  test_sends: [{ phone: "573001234567", at_ms: 5, wa_message_id: null }],
};

describe("mapBackendCampaign", () => {
  it("mapea snake → camel con narrowing de status/goal", () => {
    const c = mapBackendCampaign(backendSent);
    expect(c.status).toBe("sent");
    expect(c.goal).toBe("discount_general");
    expect(c.couponCode).toBe("PAPA20");
    expect(c.validUntil).toBe("15 de junio");
    expect(c.sendResult?.unitCostUsdMicros).toBe(12_500);
    expect(c.sendResult?.skipped[0]?.sessionId).toBe("wa_2");
    expect(c.testSends[0]?.atMs).toBe(5);
  });

  it("status/goal fuera del vocabulario caen a un default seguro", () => {
    const c = mapBackendCampaign({
      ...backendSent,
      status: "???",
      goal: "otra-cosa",
    });
    expect(c.status).toBe("draft");
    expect(c.goal).toBe("");
  });
});

describe("mapBackendStats", () => {
  it("mapea el response de /stats a camelCase", () => {
    const s = mapBackendStats({
      campaign_id: "mkt-1",
      status: "sent",
      planned: 42,
      sent: 40,
      failed_count: 2,
      skipped_count: 1,
      unit_cost_usd_micros: 12_500,
      spent_usd_micros: 500_000,
      replied: 9,
      attributed_orders: 3,
      attributed_revenue_cop: 364_500,
    });
    expect(s.attributedRevenueCop).toBe(364_500);
    expect(s.failedCount).toBe(2);
    expect(s.spentUsdMicros).toBe(500_000);
  });
});

describe("patchToBody", () => {
  it("mapea SOLO los campos presentes (PUT parcial)", () => {
    expect(patchToBody({ couponCode: "papa20", percent: 20 })).toEqual({
      coupon_code: "papa20",
      percent: 20,
    });
  });

  it("mapea message y segments completos", () => {
    expect(
      patchToBody({
        segments: ["frios"],
        message: { header: "H", body: "B", footer: "", cta: "" },
        productHandle: "duo-zodiacal",
      }),
    ).toEqual({
      segments: ["frios"],
      message: { header: "H", body: "B", footer: "", cta: "" },
      product_handle: "duo-zodiacal",
    });
  });
});
