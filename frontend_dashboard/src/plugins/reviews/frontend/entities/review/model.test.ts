/**
 * Helpers puros de la reseña: cómo se le muestran al equipo el cupón, las
 * fechas (siempre hora de Colombia), el promedio, el modo del envío y los
 * errores de la API — en español llano, sin jerga.
 */
import { describe, expect, it } from "vitest";

import { ApiError } from "@/shared/sdk";

import {
  MODE_META,
  REVIEW_TABS,
  couponChipLabel,
  formatAverage,
  formatReviewDate,
  formatShortDayBogota,
  reviewErrorDetail,
} from "./model";

/** 2026-12-12 12:00 en Bogotá (17:00 UTC). */
const DEC_12_NOON = Date.UTC(2026, 11, 12, 17, 0);
/** 2026-12-02 04:00 UTC = 1 de diciembre 23:00 en Bogotá. */
const DEC_1_LATE = 1796184000000;

describe("formatShortDayBogota", () => {
  it("día y mes corto en hora de Colombia, no en la del navegador", () => {
    expect(formatShortDayBogota(DEC_12_NOON)).toBe("12 dic");
    // En UTC ya es 2 de diciembre; en Bogotá todavía es el 1.
    expect(formatShortDayBogota(DEC_1_LATE)).toBe("1 dic");
  });
});

describe("couponChipLabel", () => {
  it("código · porcentaje · vencimiento", () => {
    expect(couponChipLabel({ code: "GRACIAS10", percent: 10, expiresAtMs: DEC_12_NOON })).toBe(
      "GRACIAS10 · 10 % · vence 12 dic",
    );
  });

  it("sin vencimiento no inventa una fecha", () => {
    expect(couponChipLabel({ code: "GRACIAS5", percent: 5, expiresAtMs: null })).toBe(
      "GRACIAS5 · 5 %",
    );
  });
});

describe("formatReviewDate", () => {
  const SUBMITTED = 1791000000000; // 2 oct 2026, 23:00 en Bogotá

  it("del año en curso: día, mes y hora de Colombia", () => {
    expect(formatReviewDate(SUBMITTED, SUBMITTED + 86_400_000)).toBe("2 oct, 23:00");
  });

  it("de otro año: lleva el año para no confundir", () => {
    expect(formatReviewDate(SUBMITTED, Date.UTC(2027, 2, 1))).toBe("2 oct 2026, 23:00");
  });

  it("la medianoche se escribe 00:xx, nunca 24:xx", () => {
    const midnight = Date.UTC(2026, 9, 3, 5, 5); // 3 oct 00:05 en Bogotá
    expect(formatReviewDate(midnight, midnight)).toBe("3 oct, 00:05");
  });
});

describe("formatAverage", () => {
  it("un decimal con coma, como se escribe en Colombia", () => {
    expect(formatAverage(4.6)).toBe("4,6");
    expect(formatAverage(5)).toBe("5,0");
    expect(formatAverage(4.333)).toBe("4,3");
  });
});

describe("MODE_META", () => {
  it("dice en palabras simples si se están enviando invitaciones", () => {
    expect(MODE_META.off.label).toBe(
      "Apagado: no se están enviando invitaciones (se enciende por Terraform)",
    );
    expect(MODE_META.test.label).toBe("Solo números de prueba");
    expect(MODE_META.on.label).toBe("Activo");
  });
});

describe("REVIEW_TABS", () => {
  it("las tres bandejas en el orden del trabajo del equipo", () => {
    expect(REVIEW_TABS.map((t) => [t.status, t.label])).toEqual([
      ["pending", "Por revisar"],
      ["approved", "Publicadas"],
      ["hidden", "Ocultas"],
    ]);
  });
});

describe("reviewErrorDetail", () => {
  it("muestra el texto que el backend escribió para el equipo (502 de la tienda)", () => {
    const err = new ApiError(502, {
      detail: "La tienda no respondió. La reseña sigue por revisar; inténtalo en un rato.",
    });
    expect(reviewErrorDetail(err)).toBe(
      "La tienda no respondió. La reseña sigue por revisar; inténtalo en un rato.",
    );
  });

  it("sin texto del backend, un mensaje llano con el número del error", () => {
    expect(reviewErrorDetail(new ApiError(500, null))).toBe(
      "Algo falló en el servidor (error 500). Inténtalo de nuevo.",
    );
  });

  it("sin conexión no muestra el error técnico del navegador", () => {
    expect(reviewErrorDetail(new TypeError("Failed to fetch"))).toBe(
      "No hubo conexión con el servidor. Inténtalo de nuevo.",
    );
  });

  it("una respuesta con forma inesperada no se disfraza de falta de conexión", () => {
    expect(reviewErrorDetail(new Error("Invalid input: expected string"))).toBe(
      "El servidor respondió algo que no se pudo leer. Inténtalo de nuevo.",
    );
  });
});
