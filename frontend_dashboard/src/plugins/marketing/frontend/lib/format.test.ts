/**
 * Formatters del plugin marketing — el costo por mensaje viaja en USD MICROS
 * (cost_unit_lesson: pricing sub-cent jamás en cents) y las ventas en COP.
 */
import { describe, expect, it } from "vitest";

import { ApiError } from "@/shared/sdk";

import {
  apiErrorDetail,
  fmtCop,
  fmtN,
  fmtUsdMicros,
  usdMicrosToCop,
} from "./format";

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

  it("cae al status cuando no hay detail", () => {
    expect(apiErrorDetail(new ApiError(502, null))).toBe("Error 502");
  });

  it("cae al message para errores no-API", () => {
    expect(apiErrorDetail(new Error("boom"))).toBe("boom");
  });
});
