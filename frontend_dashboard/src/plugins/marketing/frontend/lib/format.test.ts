/**
 * Formatters del plugin marketing — el costo por mensaje viaja en USD MICROS
 * (cost_unit_lesson: pricing sub-cent jamás en cents) y las ventas en COP.
 */
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { ApiError } from "@/shared/sdk";

import {
  apiErrorDetail,
  fmtCop,
  fmtDateMs,
  fmtDateTimeMs,
  fmtN,
  fmtUsdMicros,
  usdMicrosToCop,
} from "./format";

describe("fechas en hora de Colombia (D14)", () => {
  // El navegador del operador puede estar en otra zona (VPN, viaje): las
  // fechas del registro de cambios y de las ventas son de Bogotá igual.
  let previousTz: string | undefined;
  beforeAll(() => {
    previousTz = process.env.TZ;
    process.env.TZ = "UTC";
  });
  afterAll(() => {
    process.env.TZ = previousTz;
  });

  // 03:30 UTC del 24 = 22:30 del 23 en Bogotá.
  const LATE_NIGHT_MS = Date.parse("2026-09-24T03:30:00Z");
  const NOW_MS = Date.parse("2026-09-24T15:00:00Z");

  it("fmtDateTimeMs usa el día y la hora de Bogotá, sin el año del año en curso", () => {
    expect(fmtDateTimeMs(LATE_NIGHT_MS, NOW_MS)).toBe("23 de sept, 22:30");
  });

  it("fmtDateTimeMs pone el año cuando no es el año en curso", () => {
    const lastYear = Date.parse("2025-12-31T23:30:00Z"); // 18:30 del 31 en Bogotá
    expect(fmtDateTimeMs(lastYear, NOW_MS)).toBe("31 de dic de 2025, 18:30");
  });

  it("el año en curso es el de Bogotá (no el de UTC) al cruzar el 31 de diciembre", () => {
    // 03:00 UTC del 1-ene-2027 = 22:00 del 31-dic-2026 en Bogotá: mismo año.
    const newYearUtc = Date.parse("2027-01-01T03:00:00Z");
    expect(fmtDateTimeMs(newYearUtc, newYearUtc)).toBe("31 de dic, 22:00");
  });

  it("fmtDateMs usa el día de Bogotá", () => {
    expect(fmtDateMs(LATE_NIGHT_MS)).toBe("23 de sept de 2026");
  });
});

describe("fmtUsdMicros", () => {
  it("formatea micros → US$ con coma decimal es-CO", () => {
    expect(fmtUsdMicros(2_500_000)).toBe("US$2,50");
    expect(fmtUsdMicros(500_000)).toBe("US$0,50");
    expect(fmtUsdMicros(0)).toBe("US$0,00");
  });

  it("con 4 decimales el costo unitario sub-cent es legible", () => {
    expect(fmtUsdMicros(12_500, 4)).toBe("US$0,0125");
  });
});

describe("fmtCop / fmtN", () => {
  it("COP con puntos de miles es-CO", () => {
    expect(fmtCop(1_840_000)).toBe("$1.840.000");
    expect(fmtCop(0)).toBe("$0");
  });

  it("enteros con separador de miles", () => {
    expect(fmtN(1234)).toBe("1.234");
  });
});

describe("usdMicrosToCop", () => {
  it("convierte micros USD → COP con tasa fija 4000", () => {
    // 42 destinatarios × 12.500 micros = 525.000 micros = US$0,525 ≈ $2.100 COP
    expect(usdMicrosToCop(525_000)).toBe(2100);
  });
});

describe("apiErrorDetail", () => {
  it("extrae el detail de un ApiError FastAPI", () => {
    const err = new ApiError(404, {
      detail: "El número 300123 no tiene conversación previa con el bot",
    });
    expect(apiErrorDetail(err)).toBe(
      "El número 300123 no tiene conversación previa con el bot",
    );
  });

  it("un detail objeto con message (central de cupones) muestra el message", () => {
    const err = new ApiError(409, { detail: { message: "Ese código ya existe." } });
    expect(apiErrorDetail(err)).toBe("Ese código ya existe.");
    const field = new ApiError(422, {
      detail: { field: "percentage", message: "El descuento es un número entero entre 1 y 100." },
    });
    expect(apiErrorDetail(field)).toBe("El descuento es un número entero entre 1 y 100.");
  });

  it("cae al status cuando no hay detail", () => {
    expect(apiErrorDetail(new ApiError(502, null))).toBe("Error 502");
  });

  it("cae al message para errores no-API", () => {
    expect(apiErrorDetail(new Error("boom"))).toBe("boom");
  });
});
