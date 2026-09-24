import { describe, expect, it } from "vitest";

import { ApiError } from "@/shared/api";

import { apiErrorDetail, armLabel, customerLabel, formatUsd, worstVerdict } from "./lib";

describe("lib del laboratorio", () => {
  it("nombra cada bot como en el diseño", () => {
    expect(armLabel("A0")).toBe("Producción");
    expect(armLabel("A1")).toBe("Actual simulado");
    expect(armLabel("B")).toBe("Nuevo + Jev");
    expect(armLabel("C")).toBe("Nuevo + OpenAI");
    expect(armLabel("Z")).toBe("Z");
  });

  it("muestra al cliente sin su teléfono completo", () => {
    expect(customerLabel("wa_573001234567")).toBe("Cliente ···4567");
    expect(customerLabel("wa_abc")).toBe("Cliente ···_abc");
  });

  it("el peor veredicto de varios episodios manda", () => {
    expect(worstVerdict(["PASA", "ALERTA"])).toBe("ALERTA");
    expect(worstVerdict(["PASA", "FALLA", "ALERTA"])).toBe("FALLA");
    expect(worstVerdict(["SIN_DATOS", "PASA"])).toBe("PASA");
    expect(worstVerdict([])).toBe("SIN_DATOS");
  });

  it("formatea dólares con coma decimal", () => {
    expect(formatUsd(95)).toBe("US$95");
    expect(formatUsd(3.214)).toBe("US$3,21");
    expect(formatUsd(4.1)).toBe("US$4,1");
    expect(formatUsd(1234.5)).toBe("US$1.234,5");
    expect(formatUsd(null)).toBe("—");
  });

  it("saca el mensaje del detalle de un error del API", () => {
    expect(apiErrorDetail(new ApiError(404, { detail: "Ese turno no tiene traza en este brazo." }))).toEqual({ status: 404, message: "Ese turno no tiene traza en este brazo." });
    expect(apiErrorDetail(new ApiError(409, { detail: { message: "Ya hay una corrida en curso." } }))).toEqual({ status: 409, message: "Ya hay una corrida en curso." });
    expect(apiErrorDetail(new ApiError(500, "boom"))).toEqual({ status: 500, message: null });
    expect(apiErrorDetail(new Error("red"))).toEqual({ status: null, message: null });
  });
});
