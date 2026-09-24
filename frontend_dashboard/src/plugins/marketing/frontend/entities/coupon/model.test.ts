/**
 * Helpers puros del cupón: etiquetas de estado y de cupo, qué cupones se
 * pueden anunciar en una campaña, el código saneado y la lectura de los
 * errores de la API (campo del formulario / filas del cupo).
 */
import { describe, expect, it } from "vitest";

import { ApiError } from "@/shared/sdk";

import {
  COUPON_STATE_META,
  couponFieldError,
  couponRowErrors,
  couponUnitsLabel,
  isCouponPickable,
  sanitizeCouponCode,
  type Coupon,
} from "./model";

function makeCoupon(over: Partial<Coupon> = {}): Coupon {
  return {
    promotionId: "promo_01",
    campaignId: "procamp_01",
    code: "AMOR27",
    campaignName: "AMOR Y AMISTAD 2026",
    percentage: 10,
    products: "all",
    startsOn: "2026-09-22",
    endsOn: "2026-09-27",
    endsOnLabel: "27 de septiembre",
    status: "active",
    state: "active",
    manageable: true,
    unmanageableReason: null,
    acceptsUnits: true,
    units: null,
    ...over,
  };
}

describe("COUPON_STATE_META", () => {
  it("nombra los 5 estados como los ve el operador", () => {
    expect(
      (["draft", "scheduled", "active", "paused", "expired"] as const).map(
        (s) => COUPON_STATE_META[s].label,
      ),
    ).toEqual(["Borrador", "Programado", "Activo", "Pausado", "Vencido"]);
    expect(COUPON_STATE_META.active.tone).toBe("ok");
  });
});

describe("couponUnitsLabel", () => {
  it("quedan X de Y cuando se pudieron leer las ventas", () => {
    expect(couponUnitsLabel({ total: 5, left: 3 })).toBe("quedan 3 de 5");
  });

  it("sin ventas leídas muestra solo el total", () => {
    expect(couponUnitsLabel({ total: 5, left: null })).toBe("5 unidades");
    expect(couponUnitsLabel({ total: 1, left: null })).toBe("1 unidad");
  });
});

describe("isCouponPickable", () => {
  it("solo activos y programados se pueden anunciar en una campaña", () => {
    expect(isCouponPickable(makeCoupon({ state: "active" }))).toBe(true);
    expect(isCouponPickable(makeCoupon({ state: "scheduled" }))).toBe(true);
    for (const state of ["draft", "paused", "expired"] as const) {
      expect(isCouponPickable(makeCoupon({ state }))).toBe(false);
    }
  });
});

describe("sanitizeCouponCode", () => {
  it("mayúsculas, solo letras y números, máximo 14", () => {
    expect(sanitizeCouponCode("amor_27 ")).toBe("AMOR27");
    expect(sanitizeCouponCode("a".repeat(20))).toHaveLength(14);
  });
});

describe("couponFieldError", () => {
  it("lee el campo y el mensaje de un 422 del formulario", () => {
    const err = new ApiError(422, {
      detail: { field: "percentage", message: "El descuento es un número entero entre 1 y 100." },
    });
    expect(couponFieldError(err)).toEqual({
      field: "percentage",
      message: "El descuento es un número entero entre 1 y 100.",
    });
  });

  it("null para errores sin campo (409, 503, red)", () => {
    expect(couponFieldError(new ApiError(409, { detail: { message: "Ese código ya existe." } }))).toBeNull();
    expect(couponFieldError(new Error("boom"))).toBeNull();
  });
});

describe("couponRowErrors", () => {
  it("agrupa los mensajes por índice de fila", () => {
    const err = new ApiError(422, {
      detail: {
        message: "Revisa las filas marcadas.",
        rows: [
          { row: 0, field: "color", message: "Ese color no existe para Cubo Love." },
          { row: 0, field: "units", message: "Las unidades son un número entero desde 1." },
          { row: 2, field: "product_id", message: "Ese producto no está en el cupón." },
        ],
      },
    });
    const byRow = couponRowErrors(err);
    expect(byRow.get(0)).toHaveLength(2);
    expect(byRow.get(2)).toEqual(["Ese producto no está en el cupón."]);
    expect(byRow.has(1)).toBe(false);
  });

  it("vacío si el error no trae filas", () => {
    expect(couponRowErrors(new ApiError(503, { detail: { message: "x" } })).size).toBe(0);
  });
});
