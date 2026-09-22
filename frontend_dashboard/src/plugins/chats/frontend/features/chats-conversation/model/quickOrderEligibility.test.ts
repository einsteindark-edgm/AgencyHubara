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
    // Pasó a HUMANO desde un rechazo: no es un lead al borde de comprar.
    expect(canOfferQuickOrder([entry("RECHAZO"), entry("HUMANO")])).toBe(false);
  });

  it("se activa cuando el histórico solo tiene HUMANO", () => {
    // El operador intervino desde el arranque (o el cliente entró directo al
    // humano): no hay otro tag que descarte una venta, así que puede
    // necesitar registrar el pedido.
    expect(canOfferQuickOrder([entry("HUMANO")])).toBe(true);
    expect(canOfferQuickOrder([entry("HUMANO"), entry("HUMANO")])).toBe(true);
  });

  it("HUMANO mezclado con otros tags no cuenta como 'solo HUMANO'", () => {
    expect(canOfferQuickOrder([entry("HUMANO"), entry("RECHAZO")])).toBe(false);
    expect(canOfferQuickOrder([entry("COMPRA_EXITOSA"), entry("HUMANO")])).toBe(false);
  });

  it("tampoco con otros cierres (una compra ya cerrada, un rechazo)", () => {
    expect(canOfferQuickOrder([entry("COMPRA_EXITOSA")])).toBe(false);
    expect(canOfferQuickOrder([entry("RECHAZO")])).toBe(false);
    expect(canOfferQuickOrder([entry("CONFIRMADO_PAGO_PENDIENTE")])).toBe(false);
  });

  it("se activa cuando un lead INTERESADO pasa a HUMANO", () => {
    // El otro caso real: el cliente estaba interesado y lo escalaron (o el
    // operador intervino) para cerrar la venta a mano.
    expect(
      canOfferQuickOrder([entry("INTERESADO"), entry("HUMANO")]),
    ).toBe(true);
    // Aunque después haya más movimientos en el hilo.
    expect(
      canOfferQuickOrder([
        entry("INTERESADO"),
        entry("HUMANO"),
        entry("HUMANO"),
      ]),
    ).toBe(true);
  });

  it("se activa cuando una conversación en RETOMA_VENTA pasa a HUMANO", () => {
    // La venta se retomó (vuelta de post-venta o del humano al bot) y la
    // escalaron / la intervinieron para cerrarla a mano.
    expect(canOfferQuickOrder([entry("RETOMA_VENTA"), entry("HUMANO")])).toBe(true);
    expect(
      canOfferQuickOrder([
        entry("COMPRA_EXITOSA"),
        entry("RETOMA_VENTA"),
        entry("HUMANO"),
        entry("HUMANO"),
      ]),
    ).toBe(true);
  });

  it("se activa con RETOMA_VENTA → RECHAZO → HUMANO", () => {
    // Retomada la venta, el cliente dijo que no y el operador entró a
    // rescatarla: sigue siendo una venta a cerrar a mano.
    expect(
      canOfferQuickOrder([entry("RETOMA_VENTA"), entry("RECHAZO"), entry("HUMANO")]),
    ).toBe(true);
  });

  it("RETOMA_VENTA y HUMANO sueltos no cuentan", () => {
    expect(canOfferQuickOrder([entry("HUMANO"), entry("RETOMA_VENTA")])).toBe(false);
    // El rescate vale solo con el RECHAZO en medio, nada más.
    expect(
      canOfferQuickOrder([
        entry("RETOMA_VENTA"),
        entry("RECHAZO"),
        entry("RECHAZO"),
        entry("HUMANO"),
      ]),
    ).toBe(false);
    expect(
      canOfferQuickOrder([entry("RETOMA_VENTA"), entry("INTERESADO_FRIO"), entry("HUMANO")]),
    ).toBe(false);
    // Un RECHAZO que no viene de RETOMA_VENTA no cuenta.
    expect(canOfferQuickOrder([entry("RECHAZO"), entry("HUMANO")])).toBe(false);
  });

  it("INTERESADO y HUMANO sueltos (no uno detrás del otro) no cuentan", () => {
    // Pasó a HUMANO desde un RECHAZO: no es un lead a punto de comprar.
    expect(
      canOfferQuickOrder([entry("INTERESADO"), entry("RECHAZO"), entry("HUMANO")]),
    ).toBe(false);
    // HUMANO primero y recién después INTERESADO: nunca se escaló interesado.
    expect(canOfferQuickOrder([entry("HUMANO"), entry("INTERESADO")])).toBe(false);
  });

  it("sin histórico (sesión nueva o respuesta vieja del backend) no se activa", () => {
    expect(canOfferQuickOrder([])).toBe(false);
    expect(canOfferQuickOrder(undefined)).toBe(false);
    expect(canOfferQuickOrder(null)).toBe(false);
  });
});
