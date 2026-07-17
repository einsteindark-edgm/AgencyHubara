/**
 * Contrato Zod de la campaña de marketing — fixtures con el shape REAL del
 * backend (`src/plugins/marketing/domain/campaigns.py::new_campaign` +
 * `send_result` que persiste el CampaignSendWorkflow).
 *
 * El schema es tolerante (`.default()`): un backend viejo que no emita un
 * campo opcional NO tumba el dashboard; un shape roto (sin `id`) SÍ truena.
 */
import { describe, expect, it } from "vitest";

import {
  backendCampaignSchema,
  backendCampaignsResponseSchema,
  backendCampaignStatsSchema,
  backendSendResponseSchema,
  backendTestSendResponseSchema,
} from "./contracts";

/** Shape EXACTO que devuelve `new_campaign()` (draft recién creado). */
const draftFixture = {
  id: "mkt-a1b2c3d4e5",
  name: "Nueva campaña sin título",
  status: "draft",
  goal: "",
  percent: 0,
  coupon_code: "",
  valid_until: "",
  product_handle: null,
  segments: [],
  message: { header: "", body: "", footer: "", cta: "" },
  template_name: "campaign_promo_marketing_v1",
  schedule_at_ms: null,
  created_at_ms: 1_784_600_000_000,
  updated_at_ms: 1_784_600_000_000,
  sent_at_ms: null,
  send_result: null,
  test_sends: [],
};

/** Campaña ENVIADA con send_result + test_sends poblados. */
const sentFixture = {
  ...draftFixture,
  id: "mkt-f6g7h8i9j0",
  name: "Día del padre",
  status: "sent",
  goal: "discount_general",
  percent: 20,
  coupon_code: "PAPA20",
  valid_until: "15 de junio",
  segments: ["clientes", "interesados"],
  message: {
    header: "¡Se acerca el Día del Padre! 🎁",
    body: "Tenemos 20% de descuento en toda la tienda.",
    footer: "",
    cta: "Ver catálogo",
  },
  sent_at_ms: 1_784_700_000_000,
  send_result: {
    planned: 42,
    sent: 40,
    failed: ["wa_573001112233", "wa_573004445566"],
    skipped: [{ session_id: "wa_573007778899", reason: "excluido" }],
    unit_cost_usd_micros: 12_500,
    spent_usd_micros: 500_000,
  },
  test_sends: [
    { phone: "573001234567", at_ms: 1_784_650_000_000, wa_message_id: "wamid.X" },
  ],
};

describe("backendCampaignSchema", () => {
  it("parsea el draft recién creado (shape exacto de new_campaign)", () => {
    const parsed = backendCampaignSchema.parse(draftFixture);
    expect(parsed.id).toBe("mkt-a1b2c3d4e5");
    expect(parsed.status).toBe("draft");
    expect(parsed.send_result).toBeNull();
    expect(parsed.test_sends).toEqual([]);
  });

  it("parsea la campaña enviada con send_result + test_sends", () => {
    const parsed = backendCampaignSchema.parse(sentFixture);
    expect(parsed.send_result?.planned).toBe(42);
    expect(parsed.send_result?.failed).toHaveLength(2);
    expect(parsed.send_result?.skipped[0]?.reason).toBe("excluido");
    expect(parsed.test_sends[0]?.wa_message_id).toBe("wamid.X");
  });

  it("tolera un backend viejo sin campos opcionales (defaults)", () => {
    const minimal = {
      id: "mkt-old",
      name: "Vieja",
      status: "draft",
      created_at_ms: 1,
      updated_at_ms: 1,
    };
    const parsed = backendCampaignSchema.parse(minimal);
    expect(parsed.goal).toBe("");
    expect(parsed.percent).toBe(0);
    expect(parsed.segments).toEqual([]);
    expect(parsed.message).toEqual({ header: "", body: "", footer: "", cta: "" });
    expect(parsed.send_result).toBeNull();
    expect(parsed.test_sends).toEqual([]);
    expect(parsed.schedule_at_ms).toBeNull();
  });

  it("parsea la curaduría manual: excluded/extra session ids", () => {
    const parsed = backendCampaignSchema.parse({
      ...draftFixture,
      excluded_session_ids: ["wa_573001112233"],
      extra_session_ids: ["wa_+573009998877"],
    });
    expect(parsed.excluded_session_ids).toEqual(["wa_573001112233"]);
    expect(parsed.extra_session_ids).toEqual(["wa_+573009998877"]);
  });

  it("defaultea excluded/extra a [] si el backend no los emite", () => {
    const parsed = backendCampaignSchema.parse(draftFixture);
    expect(parsed.excluded_session_ids).toEqual([]);
    expect(parsed.extra_session_ids).toEqual([]);
  });

  it("rechaza un shape sin id (contract drift visible, no silencioso)", () => {
    const withoutId: Record<string, unknown> = { ...draftFixture };
    delete withoutId.id;
    expect(() => backendCampaignSchema.parse(withoutId)).toThrow();
  });
});

describe("backendCampaignsResponseSchema", () => {
  it("parsea el envelope {campaigns: [...]}", () => {
    const parsed = backendCampaignsResponseSchema.parse({
      campaigns: [draftFixture, sentFixture],
    });
    expect(parsed.campaigns).toHaveLength(2);
  });
});

describe("backendSendResponseSchema / backendTestSendResponseSchema", () => {
  it("parsea la respuesta de POST /send", () => {
    const parsed = backendSendResponseSchema.parse({
      workflow_id: "campaign-send-mkt-a1",
      run_id: "run-1",
      scheduled: false,
    });
    expect(parsed.scheduled).toBe(false);
  });

  it("parsea la respuesta de POST /test", () => {
    const parsed = backendTestSendResponseSchema.parse({
      ok: true,
      session_id: "wa_573001234567",
    });
    expect(parsed.ok).toBe(true);
  });
});

describe("backendCampaignStatsSchema", () => {
  it("parsea stats de campaña enviada (shape real del endpoint /stats)", () => {
    const parsed = backendCampaignStatsSchema.parse({
      campaign_id: "mkt-f6g7h8i9j0",
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
    expect(parsed.replied).toBe(9);
    expect(parsed.attributed_revenue_cop).toBe(364_500);
  });

  it("tolera stats de campaña NO enviada (send_result vacío ⇒ nulls)", () => {
    const parsed = backendCampaignStatsSchema.parse({
      campaign_id: "mkt-a1b2c3d4e5",
      status: "draft",
      planned: null,
      sent: null,
      failed_count: 0,
      skipped_count: 0,
      unit_cost_usd_micros: null,
      spent_usd_micros: null,
      replied: 0,
      attributed_orders: 0,
      attributed_revenue_cop: 0,
    });
    expect(parsed.planned).toBeNull();
    expect(parsed.spent_usd_micros).toBeNull();
  });
});
