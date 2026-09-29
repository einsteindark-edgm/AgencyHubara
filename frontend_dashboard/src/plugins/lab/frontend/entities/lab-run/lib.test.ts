import { describe, expect, it } from "vitest";

import { ApiError } from "@/shared/api";

import {
  apiErrorDetail,
  armLabel,
  capabilityLabel,
  customerLabel,
  decidedByLabel,
  decisionStageLabel,
  engineDecisionsOf,
  formatDecisionValue,
  formatUsd,
  worstVerdict,
} from "./lib";

describe("lib del laboratorio", () => {
  it("nombra cada bot como en el diseño", () => {
    expect(armLabel("A0")).toBe("Producción");
    expect(armLabel("A1")).toBe("Actual simulado");
    // B0: el workflow nuevo (V2) con las reglas de hoy; tiene que dar lo mismo que A1.
    expect(armLabel("B0")).toBe("Nuevo sin Jev");
    expect(armLabel("B")).toBe("Nuevo + Jev");
    // El rival OpenAI (brazo C) se quitó el 2026-09-28: 100 % Jev.
    expect(armLabel("C")).toBe("C");
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

  it("el costo por turno se ve con 4 decimales (el clasificador cuesta US$0,0002)", () => {
    expect(formatUsd(0.0192, 4)).toBe("US$0,0192");
    expect(formatUsd(0.0002, 4)).toBe("US$0,0002");
    expect(formatUsd(0.0002)).toBe("US$0");
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

describe("decisiones del motor en un turno (bot nuevo)", () => {
  it("lee las decisiones de la traza y descarta las que no tienen forma", () => {
    const rows = engineDecisionsOf({
      decisions: [
        { stage: "ingest", message: 1, capability: "compra", by: "jev", provider: "jev", value: "no", rule: "si" },
        "basura",
        { stage: "turno", capability: "datos", by: "respaldo", provider: "jev", value: true, reason: "timeout" },
      ],
    });

    expect(rows.map((r) => r.capability)).toEqual(["compra", "datos"]);
    expect(engineDecisionsOf({})).toEqual([]);
    expect(engineDecisionsOf({ decisions: "no es una lista" })).toEqual([]);
  });

  it("dice quién decidió y, si fue la regla con Jev encendido, por qué", () => {
    expect(decidedByLabel({ by: "jev", provider: "jev" })).toBe("Jev");
    expect(decidedByLabel({ by: "piso", provider: "jev" })).toBe("Piso de la regla");
    expect(decidedByLabel({ by: "reglas", provider: "reglas" })).toBe("Regla");
    expect(decidedByLabel({ by: "reglas", provider: "sombra" })).toBe("Regla (Jev en sombra)");
    expect(decidedByLabel({ by: "respaldo", provider: "jev", reason: "duda" })).toBe("Regla (Jev dudó)");
    expect(decidedByLabel({ by: "respaldo", provider: "jev", reason: "no_question" })).toBe("Regla (nada que preguntar)");
    expect(decidedByLabel({ by: "respaldo", provider: "jev", reason: "timeout" })).toBe("Regla (Jev falló: timeout)");
  });

  it("nombra la capacidad como el panel del motor y la etapa del turno", () => {
    expect(capabilityLabel("familia_de_color")).toBe("Familia de color");
    expect(capabilityLabel("acuse")).toBe("Acuse tras la despedida");
    expect(capabilityLabel("preambulo")).toBe("Preámbulo del modelo");
    expect(capabilityLabel("una_nueva")).toBe("una_nueva");
    expect(decisionStageLabel({ stage: "ingest", message: 2 })).toBe("Lectura del mensaje 2");
    expect(decisionStageLabel({ stage: "turno" })).toBe("Turno");
    expect(decisionStageLabel({ stage: "complemento" })).toBe("Complemento");
  });

  it("muestra el valor decidido de forma legible", () => {
    expect(formatDecisionValue(true)).toBe("sí");
    expect(formatDecisionValue(false)).toBe("no");
    expect(formatDecisionValue(["lila", "azul"])).toBe("lila, azul");
    expect(formatDecisionValue(null)).toBe("—");
    expect(formatDecisionValue("")).toBe("—");
    expect(formatDecisionValue({ fecha: "2026-10-05" })).toBe('{"fecha":"2026-10-05"}');
  });
});
