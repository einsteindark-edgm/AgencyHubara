import { describe, it, expect } from "vitest";

import { canOfferQuickOrder, PENDING_SHIPPING_DATA_TAG } from "./quickOrderEligibility";

const entry = (tag: string) => ({
  tag,
  motivo: "x",
  active_route: "humano",
  timestamp: 1,
});

describe("canOfferQuickOrder", () => {
  it("se activa cuando el histórico tiene CONFIRMADO_SIN_DATOS", () => {
    expect(
      canOfferQuickOrder([entry("INTERESADO"), entry(PENDING_SHIPPING_DATA_TAG), entry("HUMANO")]),
    ).toBe(true);
  });

  it("NO se activa en una conversación normal intervenida", () => {
    // El caso común: el operador entra a responder una duda. No hay pedido
    // confirmado sin datos, así que el botón sería ruido.
    expect(canOfferQuickOrder([entry("INTERESADO"), entry("HUMANO")])).toBe(false);
  });

  it("tampoco con otros cierres (una compra ya cerrada, un rechazo)", () => {
    expect(canOfferQuickOrder([entry("COMPRA_EXITOSA")])).toBe(false);
    expect(canOfferQuickOrder([entry("RECHAZO")])).toBe(false);
    expect(canOfferQuickOrder([entry("CONFIRMADO_PAGO_PENDIENTE")])).toBe(false);
  });

  it("sin histórico (sesión nueva o respuesta vieja del backend) no se activa", () => {
    expect(canOfferQuickOrder([])).toBe(false);
    expect(canOfferQuickOrder(undefined)).toBe(false);
    expect(canOfferQuickOrder(null)).toBe(false);
  });
});
