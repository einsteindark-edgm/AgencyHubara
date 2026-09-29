import { describe, expect, it } from "vitest";

import { ApiError } from "@/shared/api";

import { checkCatalogSchema, evalResultSchema } from "./contracts";
import {
  apiErrorDetail,
  armLabel,
  capabilityLabel,
  checkView,
  customerLabel,
  decidedByLabel,
  decisionStageLabel,
  engineDecisionsOf,
  formatDecisionValue,
  formatUsd,
  turnVerdict,
  worstVerdict,
} from "./lib";

describe("lib del laboratorio", () => {
  it("nombra cada bot en palabras del operador", () => {
    expect(armLabel("A0")).toBe("Producción");
    expect(armLabel("A1")).toBe("Bot actual simulado");
    // B0: el workflow nuevo (V2) con las reglas de hoy; tiene que dar lo mismo que A1.
    expect(armLabel("B0")).toBe("Bot nuevo sin Jev");
    expect(armLabel("B")).toBe("Bot nuevo con Jev");
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
    expect(decidedByLabel({ by: "piso", provider: "jev" })).toBe("La regla corrigió a Jev");
    expect(decidedByLabel({ by: "reglas", provider: "reglas" })).toBe("La regla");
    expect(decidedByLabel({ by: "reglas", provider: "sombra" })).toBe("La regla (Jev en sombra)");
    expect(decidedByLabel({ by: "respaldo", provider: "jev", reason: "duda" })).toBe("La regla (Jev dudó)");
    expect(decidedByLabel({ by: "respaldo", provider: "jev", reason: "no_question" })).toBe("La regla (nada que preguntarle a Jev)");
    expect(decidedByLabel({ by: "respaldo", provider: "jev", reason: "timeout" })).toBe("La regla (Jev no respondió a tiempo)");
    expect(decidedByLabel({ by: "respaldo", provider: "jev", reason: "no_api_key" })).toBe("La regla (falta la llave de Jev)");
    expect(decidedByLabel({ by: "respaldo", provider: "jev", reason: "http_402" })).toBe("La regla (Jev sin saldo)");
    expect(decidedByLabel({ by: "respaldo", provider: "jev", reason: "bad_shape" })).toBe("La regla (Jev falló: bad_shape)");
  });

  it("nombra la capacidad como el panel del motor y la etapa del turno", () => {
    expect(capabilityLabel("familia_de_color")).toBe("Familia de color");
    expect(capabilityLabel("acuse")).toBe("Acuse tras la despedida");
    expect(capabilityLabel("preambulo")).toBe("Preámbulo del modelo");
    expect(capabilityLabel("una_nueva")).toBe("una_nueva");
    expect(decisionStageLabel({ stage: "ingest", message: 2 })).toBe("Al leer el mensaje 2");
    expect(decisionStageLabel({ stage: "turno" })).toBe("Durante el turno");
    expect(decisionStageLabel({ stage: "complemento" })).toBe("En el mensaje de complemento");
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

describe("checks por su nombre (el operador no entiende «DES-04 · falla»)", () => {
  const catalog = checkCatalogSchema.parse({
    registry_version: 4,
    checks: [
      { id: "DES-06", name: "Sin datos de catálogo inventados", level: "critico", kind: "judge", applies: "El bot afirmó productos.", rule: "Todo lo afirmado existe en el catálogo real vigente." },
      { id: "EST-06", name: "Sin narración descartada", level: "menor", kind: "code", applies: "Siempre.", rule: "Ningún texto del LLM se descarta." },
      { id: "APE-01", name: "Saludo por hora y marca en el primer contacto", level: "mayor", kind: "code", applies: "Primer contacto.", rule: "Abre con saludo por hora." },
    ],
  });
  const result = (raw: Record<string, unknown>) => evalResultSchema.parse(raw);

  it("muestra el nombre, lo que se esperaba y por qué falló", () => {
    const view = checkView(catalog, result({ check_id: "DES-06", verdict: "falla", turn: 11, source: "judge", level: "mayor",
      evidence: "La figura femenina con la vasija no la manejamos.", critique: "Falso: la Luz Serena sí está en el catálogo." }));

    expect(view.name).toBe("Sin datos de catálogo inventados");
    expect(view.rule).toBe("Todo lo afirmado existe en el catálogo real vigente.");
    expect(view.reason).toBe("Falso: la Luz Serena sí está en el catálogo.");
    expect(view.quote).toBe("La figura femenina con la vasija no la manejamos.");
    expect(view.byJudge).toBe(true);
  });

  it("el nivel es el que contó para el veredicto y se explica si el juez lo bajó", () => {
    // Un check de juez crítico sin calibrar cuenta como mayor (scorecard/verdict.py).
    const judged = checkView(catalog, result({ check_id: "DES-06", verdict: "falla", turn: 3, level: "mayor" }));
    expect(judged.level).toBe("mayor");
    expect(judged.levelNote).toBe("Es crítico, pero cuenta como mayor mientras el juez no esté calibrado.");

    const plain = checkView(catalog, result({ check_id: "EST-06", verdict: "falla", turn: 1 }));
    expect(plain.level).toBe("menor");
    expect(plain.levelNote).toBeNull();
  });

  it("la evidencia de un check de código no repite el turno", () => {
    const view = checkView(catalog, result({ check_id: "APE-01", verdict: "falla", turn: 1, source: "code",
      evidence: "turno 1: primer texto sin saludo por hora ni marca Hubara «Tenemos 4 piezas»" }));

    expect(view.reason).toBe("Primer texto sin saludo por hora ni marca Hubara «Tenemos 4 piezas»");
    expect(view.quote).toBeNull();
    expect(view.byJudge).toBe(false);
  });

  it("un código que el registro no conoce se muestra tal cual, sin romper la vista", () => {
    const view = checkView(catalog, result({ check_id: "ZZZ-99", verdict: "falla", turn: 2 }));
    expect(view.name).toBe("ZZZ-99");
    expect(view.rule).toBe("");
    expect(checkView(undefined, result({ check_id: "EST-06", verdict: "pasa", turn: 2 })).name).toBe("EST-06");
  });

  it("el resultado de un turno sigue la regla del scorecard: crítico falla, mayor alerta", () => {
    const rows = (raw: Array<Record<string, unknown>>) => raw.map(result);

    expect(turnVerdict(catalog, rows([{ check_id: "EST-06", verdict: "falla", turn: 1 }, { check_id: "APE-01", verdict: "pasa", turn: 1 }]))).toBe("PASA");
    expect(turnVerdict(catalog, rows([{ check_id: "APE-01", verdict: "falla", turn: 1 }]))).toBe("ALERTA");
    expect(turnVerdict(catalog, rows([{ check_id: "DES-06", verdict: "falla", turn: 1, level: "critico" }]))).toBe("FALLA");
    // El registro dice crítico, pero el resultado guardado contó como mayor.
    expect(turnVerdict(catalog, rows([{ check_id: "DES-06", verdict: "falla", turn: 1, level: "mayor" }]))).toBe("ALERTA");
    expect(turnVerdict(catalog, rows([{ check_id: "APE-01", verdict: "sin_senal", turn: 1 }]))).toBe("SIN_DATOS");
  });
});
