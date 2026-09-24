/**
 * Contrato de `POST /api/chats/order-intake/{session}/suggest` (cupo por
 * unidad, fase 6 de CUPONES_PLAN): cada ítem trae el color/aroma resuelto del
 * borrador del chat, las listas CERRADAS del producto y cuántas unidades de
 * la línea llevan el descuento del cupón. Un backend viejo (sin esos campos)
 * tiene que seguir parseando: el formulario abre igual, sin selectores.
 */
import { describe, expect, it } from "vitest";

import { createOrderResultSchema, orderSuggestionSchema } from "./contracts";

const BASE = {
  session_key: "wa_golden_intake",
  phone_number: "570000000000",
  shipping: {},
};

describe("orderSuggestionSchema · ítems con color, aroma y cupo", () => {
  it("parsea el color/aroma resuelto, las listas del producto y el cupo de la línea", () => {
    const parsed = orderSuggestionSchema.parse({
      ...BASE,
      coupon_code: "AMOR26",
      discount_cop: 2100,
      items: [
        {
          handle: "cubo-love",
          title: "Cubo Love",
          variant_label: null,
          quantity: 2,
          unit_price_cop: 21000,
          line_total_cop: 42000,
          variant_resolved: true,
          evidence: null,
          color: "Rosado",
          aroma: "Café",
          colors: ["Rosado", "Azul"],
          aromas: ["Café", "Lavanda"],
          coupon_units: 1,
          coupon_discount_cop: 2100,
        },
      ],
    });

    expect(parsed.items[0]).toMatchObject({
      color: "Rosado",
      aroma: "Café",
      colors: ["Rosado", "Azul"],
      aromas: ["Café", "Lavanda"],
      coupon_units: 1,
      coupon_discount_cop: 2100,
    });
  });

  it("un backend sin esos campos sigue parseando: sin listas y sin cupo en la línea", () => {
    const parsed = orderSuggestionSchema.parse({
      ...BASE,
      items: [
        {
          handle: "duo-zodiacal",
          title: "Dúo Zodiacal",
          quantity: 1,
          unit_price_cop: 52000,
          line_total_cop: 52000,
        },
      ],
    });

    expect(parsed.items[0]).toMatchObject({
      color: null,
      aroma: null,
      colors: [],
      aromas: [],
      coupon_units: 0,
      coupon_discount_cop: 0,
    });
  });
});

describe("createOrderResultSchema · rechazos del cupo por unidad", () => {
  it("conserva los `problems` de un color/aroma que el producto no tiene", () => {
    const parsed = createOrderResultSchema.parse({
      registered: false,
      error_detail: "invalid_variant_attribute",
      problems: ['Cubo Love no tiene el color "Verde" (opciones: Rosado, Azul)'],
    });

    expect(parsed.error_detail).toBe("invalid_variant_attribute");
    expect(parsed.problems).toEqual([
      'Cubo Love no tiene el color "Verde" (opciones: Rosado, Azul)',
    ]);
  });
});

describe("orderSuggestionSchema · catálogo con listas de color y aroma", () => {
  it("parsea las listas de cada producto del catálogo y las deja vacías si no vienen", () => {
    const parsed = orderSuggestionSchema.parse({
      ...BASE,
      catalog: [
        {
          handle: "cubo-love",
          title: "Cubo Love",
          variants: [{ label: "", unit_price_cop: 21000 }],
          colors: ["Rosado", "Azul"],
          aromas: ["Café"],
        },
        { handle: "luz-serena", title: "Luz Serena", variants: [] },
      ],
    });

    expect(parsed.catalog[0]).toMatchObject({ colors: ["Rosado", "Azul"], aromas: ["Café"] });
    expect(parsed.catalog[1]).toMatchObject({ colors: [], aromas: [] });
  });
});

describe("createOrderResultSchema · quota_changed trae el total nuevo", () => {
  it("conserva el descuento y el total recalculados", () => {
    const parsed = createOrderResultSchema.parse({
      registered: false,
      order_id: null,
      error_detail: "quota_changed",
      subtotal_cop: 42000,
      shipping_cop: 7900,
      discount_cop: 4200,
      total_cop: 45700,
    });

    expect(parsed).toMatchObject({ discount_cop: 4200, total_cop: 45700 });
  });
});
