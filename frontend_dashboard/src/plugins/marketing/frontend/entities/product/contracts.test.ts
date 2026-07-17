/**
 * Contrato Zod de GET /api/marketing/products — fixture con el shape REAL
 * del endpoint (`api/__init__.py::list_products`, snapshot del catálogo):
 * cada campo derivado de variante/precio puede venir null.
 */
import { describe, expect, it } from "vitest";

import { backendProductsResponseSchema } from "./contracts";

const fixture = {
  products: [
    {
      handle: "duo-zodiacal",
      title: "Duo Zodiacal",
      sku: "DUO-ZOD-01",
      category: "Velas",
      price_amount: "89000",
      currency: "cop",
      thumbnail: "https://cdn.example.com/duo.jpg",
    },
    {
      handle: "vela-sin-variante",
      title: "Vela sin variante",
      sku: null,
      category: null,
      price_amount: null,
      currency: null,
      thumbnail: null,
    },
  ],
};

describe("backendProductsResponseSchema", () => {
  it("parsea productos con y sin variante/precio", () => {
    const parsed = backendProductsResponseSchema.parse(fixture);
    expect(parsed.products).toHaveLength(2);
    expect(parsed.products[0]?.price_amount).toBe("89000");
    expect(parsed.products[1]?.sku).toBeNull();
  });

  it("tolera price_amount numérico (drift del catalog client)", () => {
    const parsed = backendProductsResponseSchema.parse({
      products: [{ handle: "x", title: "X", sku: null, category: null, price_amount: 89000, currency: "cop", thumbnail: null }],
    });
    expect(parsed.products[0]?.price_amount).toBe(89000);
  });
});
