/**
 * Lógica pura del dominio campaña: la línea de oferta del preview DEBE
 * espejar `_campaign_offer_line` del backend (misma frase, mismos casos) y
 * el checklist de validación refleja lo que exige POST /send (422).
 */
import { describe, expect, it } from "vitest";

import {
  campaignChecklist,
  campaignOfferLine,
  goalNeedsProduct,
  goalUsesDiscount,
  isCampaignEditable,
  OPT_OUT_LINE,
  type Campaign,
} from "./model";

function makeCampaign(over: Partial<Campaign> = {}): Campaign {
  return {
    id: "mkt-1",
    name: "Campaña",
    status: "draft",
    goal: "",
    percent: 0,
    couponCode: "",
    validUntil: "",
    productHandle: null,
    segments: [],
    message: { header: "", body: "", footer: "", cta: "" },
    templateName: "campaign_promo_marketing_v1",
    scheduleAtMs: null,
    createdAtMs: 1,
    updatedAtMs: 1,
    sentAtMs: null,
    sendResult: null,
    testSends: [],
    excludedSessionIds: [],
    extraSessionIds: [],
    ...over,
  };
}

describe("campaignOfferLine — espejo de _campaign_offer_line del backend", () => {
  it("cupón con vigencia", () => {
    expect(
      campaignOfferLine(makeCampaign({ couponCode: "PAPA20", validUntil: "15 de junio" })),
    ).toBe("Usa el código PAPA20 al pagar — válido hasta 15 de junio.");
  });

  it("cupón sin vigencia", () => {
    expect(campaignOfferLine(makeCampaign({ couponCode: "PAPA20" }))).toBe(
      "Usa el código PAPA20 al pagar.",
    );
  });

  it("porcentaje sin cupón", () => {
    expect(campaignOfferLine(makeCampaign({ percent: 20 }))).toBe(
      "Aprovecha el 20% de descuento. Escríbeme aquí y te muestro el catálogo.",
    );
  });

  it("lanzamiento sin descuento", () => {
    expect(campaignOfferLine(makeCampaign({ goal: "launch" }))).toBe(
      "Escríbeme aquí y te cuento más.",
    );
  });
});

describe("reglas por objetivo", () => {
  it("producto requerido para discount_product y launch", () => {
    expect(goalNeedsProduct("discount_product")).toBe(true);
    expect(goalNeedsProduct("launch")).toBe(true);
    expect(goalNeedsProduct("discount_general")).toBe(false);
  });

  it("descuento aplica salvo launch", () => {
    expect(goalUsesDiscount("discount_general")).toBe(true);
    expect(goalUsesDiscount("launch")).toBe(false);
    expect(goalUsesDiscount("")).toBe(false);
  });
});

describe("isCampaignEditable — espejo de _EDITABLE_STATUSES", () => {
  it.each(["draft", "scheduled"] as const)("%s es editable", (s) => {
    expect(isCampaignEditable(s)).toBe(true);
  });
  it.each(["sending", "sent", "failed"] as const)("%s NO es editable", (s) => {
    expect(isCampaignEditable(s)).toBe(false);
  });
});

describe("campaignChecklist", () => {
  it("campaña vacía: requeridos pendientes, cupón opcional", () => {
    const items = campaignChecklist(makeCampaign());
    const byKey = Object.fromEntries(items.map((i) => [i.key, i]));
    expect(byKey.goal?.done).toBe(false);
    expect(byKey.message?.done).toBe(false);
    expect(byKey.audience?.done).toBe(false);
    expect(byKey.coupon?.required).toBe(false);
  });

  it("campaña de producto exige el producto elegido", () => {
    const items = campaignChecklist(
      makeCampaign({ goal: "discount_product", percent: 10 }),
    );
    const product = items.find((i) => i.key === "product");
    expect(product?.required).toBe(true);
    expect(product?.done).toBe(false);
  });

  it("campaña completa: todos los requeridos en done", () => {
    const items = campaignChecklist(
      makeCampaign({
        goal: "discount_general",
        percent: 20,
        segments: ["clientes"],
        message: { header: "", body: "Hay 20% off", footer: "", cta: "" },
      }),
    );
    expect(items.filter((i) => i.required).every((i) => i.done)).toBe(true);
  });
});

describe("OPT_OUT_LINE", () => {
  it("es el texto fijo del template aprobado", () => {
    expect(OPT_OUT_LINE).toBe(
      'Si prefieres no recibir más promociones, respóndeme "NO MÁS" y te doy de baja.',
    );
  });
});
