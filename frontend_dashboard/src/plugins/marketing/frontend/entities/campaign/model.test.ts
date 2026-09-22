/**
 * Lógica pura del dominio campaña: la línea de oferta del preview DEBE
 * espejar `_campaign_offer_line` del backend (misma frase, mismos casos) y
 * el checklist de validación refleja lo que exige POST /send (422).
 */
import { describe, expect, it } from "vitest";

import {
  campaignChecklist,
  campaignMessageLine,
  campaignOfferLine,
  carouselSizeError,
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
    segments: [],
    message: { header: "", body: "" },
    templateName: "campaign_promo_marketing_v1",
    scheduleAtMs: null,
    createdAtMs: 1,
    updatedAtMs: 1,
    sentAtMs: null,
    sendResult: null,
    testSends: [],
    excludedSessionIds: [],
    extraSessionIds: [],
    importedContacts: [],
    carouselHandles: [],
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

  it("producto existente / lanzamiento NO exigen un producto único (los productos van en el carrusel)", () => {
    for (const goal of ["discount_product", "launch"] as const) {
      const items = campaignChecklist(
        makeCampaign({
          goal,
          percent: goal === "launch" ? 0 : 10,
          message: { header: "", body: "Hola" },
          segments: ["clientes"],
        }),
      );
      expect(items.find((i) => i.key === "product")).toBeUndefined();
      expect(items.filter((i) => i.required).every((i) => i.done)).toBe(true);
    }
  });

  it("la audiencia se cumple con contactos importados aunque no haya segmentos", () => {
    const items = campaignChecklist(
      makeCampaign({
        goal: "launch",
        message: { header: "", body: "Hola" },
        importedContacts: [{ phone: "573001234567", name: null }],
      }),
    );
    const audience = items.find((i) => i.key === "audience");
    expect(audience?.done).toBe(true);
    expect(audience?.label).toBe("Audiencia elegida");
  });

  it("carrusel con 1 producto bloquea el envío; con 2..10 o ninguno, no", () => {
    const one = campaignChecklist(makeCampaign({ carouselHandles: ["a"] }));
    const item = one.find((i) => i.key === "carousel");
    expect(item?.required).toBe(true);
    expect(item?.done).toBe(false);
    expect(
      campaignChecklist(makeCampaign({ carouselHandles: ["a", "b"] })).find(
        (i) => i.key === "carousel",
      )?.done,
    ).toBe(true);
    expect(
      campaignChecklist(makeCampaign()).find((i) => i.key === "carousel"),
    ).toBeUndefined();
    expect(carouselSizeError(["a"])).toMatch(/entre 2 y 10/);
    expect(carouselSizeError([])).toBeNull();
    expect(carouselSizeError(Array.from({ length: 11 }, (_, i) => `p${i}`))).toMatch(
      /entre 2 y 10/,
    );
  });


  it("campaña completa: todos los requeridos en done", () => {
    const items = campaignChecklist(
      makeCampaign({
        goal: "discount_general",
        percent: 20,
        segments: ["clientes"],
        message: { header: "", body: "Hay 20% off" },
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

describe("campaignMessageLine — el mensaje tal cual lo recibe el cliente", () => {
  it("une encabezado y cuerpo en una oración, como el envío", () => {
    expect(
      campaignMessageLine({ header: "¡Se acerca el Día del Padre!", body: "Tenemos 20% off." }),
    ).toBe("¡Se acerca el Día del Padre! Tenemos 20% off.");
  });

  it("encabezado sin puntuación final recibe punto", () => {
    expect(campaignMessageLine({ header: "Nueva colección", body: "Ya llegó." })).toBe(
      "Nueva colección. Ya llegó.",
    );
  });

  it("colapsa saltos de línea y espacios (Meta no los acepta en variables)", () => {
    expect(campaignMessageLine({ header: "", body: "Hola\n\nvelas    nuevas\t" })).toBe(
      "Hola velas nuevas",
    );
  });

  it("vacío si no hay ni encabezado ni cuerpo", () => {
    expect(campaignMessageLine({ header: "  ", body: "" })).toBe("");
  });
});
