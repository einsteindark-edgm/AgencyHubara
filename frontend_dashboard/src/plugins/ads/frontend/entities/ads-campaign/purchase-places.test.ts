/**
 * Dónde compran (2026-10-23): compras por ciudad y zona, de una campaña o de
 * todas (`GET /api/ads/purchase-places`). Acá: el contrato Zod (defensa contra
 * drift) y el mapeo a dominio.
 */

import { describe, expect, it } from "vitest";

import { mapPurchasePlaces } from "./api";

const raw = {
  orders: 3,
  paid_orders: 2,
  value_cop: 130000,
  without_city: 1,
  overall_orders: 9,
  cities: [
    {
      key: "bogota",
      city: "Bogotá",
      orders: 2,
      paid_orders: 1,
      value_cop: 90000,
      overall_orders: 5,
      zones: [
        { zone: "Chapinero", orders: 1, value_cop: 60000 },
        { zone: null, orders: 1, value_cop: 30000 },
      ],
      campaigns: [{ id: "CMP_1", name: "Velitas", orders: 2, value_cop: 90000 }],
    },
  ],
  orders_available: true,
  truncated: false,
};

describe("mapPurchasePlaces", () => {
  it("mapea totales, ciudades, zonas y campañas", () => {
    const out = mapPurchasePlaces(raw);

    expect(out).toMatchObject({
      orders: 3,
      paidOrders: 2,
      valueCop: 130000,
      withoutCity: 1,
      overallOrders: 9,
      ordersAvailable: true,
      truncated: false,
    });
    expect(out.cities).toEqual([
      {
        key: "bogota",
        city: "Bogotá",
        orders: 2,
        paidOrders: 1,
        valueCop: 90000,
        overallOrders: 5,
        zones: [
          { zone: "Chapinero", orders: 1, valueCop: 60000 },
          { zone: null, orders: 1, valueCop: 30000 },
        ],
        campaigns: [{ id: "CMP_1", name: "Velitas", orders: 2, valueCop: 90000 }],
      },
    ]);
  });

  it("rechaza una respuesta con forma distinta", () => {
    expect(() => mapPurchasePlaces({ orders: "3" })).toThrow();
  });
});
