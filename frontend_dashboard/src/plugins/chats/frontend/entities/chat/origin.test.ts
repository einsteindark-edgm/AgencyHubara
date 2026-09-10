/**
 * `formatOrigin`: etiqueta humana del origen real de la conversación.
 * Prioridad Meta: nombre real de campaña (Graph) > headline del referral > ad id.
 */
import { describe, it, expect } from "vitest";
import { formatOrigin } from "./api";

const base = {
  channel: "ad",
  source_id: "AD_001",
  source_type: "ad",
  headline: "Velas aromáticas",
  first_seen_ms: 1789006000000,
  campaign_name: null,
  ad_name: null,
};

describe("formatOrigin", () => {
  it("usa el nombre real de la campaña cuando Graph lo resolvió", () => {
    expect(formatOrigin({ ...base, campaign_name: "Día del Padre", ad_name: "Velas CTA" })).toEqual({
      label: "Meta Ads · Día del Padre",
      detail: "Velas CTA",
      isMeta: true,
    });
  });

  it("degrada al headline del referral y muestra el ad id como detalle", () => {
    expect(formatOrigin(base)).toEqual({
      label: "Meta Ads · Velas aromáticas",
      detail: "ad AD_001",
      isMeta: true,
    });
  });

  it("distingue post de ad", () => {
    expect(formatOrigin({ ...base, channel: "post" }).label).toBe("Meta post · Velas aromáticas");
  });

  it("clasifica los canales no-Meta del ingest", () => {
    expect(formatOrigin({ ...base, channel: "direct" })).toEqual({
      label: "Directo (escribió al número)",
      isMeta: false,
    });
    expect(formatOrigin({ ...base, channel: "web_cart" }).label).toBe("Carrito web");
    expect(formatOrigin({ ...base, channel: "web_referral" }).label).toBe("Link web / WhatsApp");
  });

  it("sin origen → Sin dato", () => {
    expect(formatOrigin(null)).toEqual({ label: "Sin dato", isMeta: false });
    expect(formatOrigin({ ...base, channel: null }).label).toBe("Sin dato");
  });
});
