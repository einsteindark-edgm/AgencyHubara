/**
 * Aritmética pura del calendario del inbox. Vive separada del componente
 * porque el borde de mes y la selección de rango son donde se esconden los
 * bugs, y en un `render()` cuestan diez veces más de verificar.
 */
import { describe, expect, it } from "vitest";
import { buildMonthGrid, monthLabelEs, nextRange, shiftMonth } from "./calendar";

describe("buildMonthGrid", () => {
  it("arranca en LUNES (convención colombiana), no en domingo", () => {
    // 2026-09-01 cae martes → la primera celda es el lunes 31 de agosto.
    expect(buildMonthGrid("2026-09")[0][0]).toEqual({
      iso: "2026-08-31",
      inMonth: false,
    });
  });

  it("devuelve semanas completas de 7 días", () => {
    for (const week of buildMonthGrid("2026-09")) expect(week).toHaveLength(7);
  });

  it("cubre el mes entero sin huecos", () => {
    const days = buildMonthGrid("2026-09")
      .flat()
      .filter((d) => d.inMonth)
      .map((d) => d.iso);
    expect(days[0]).toBe("2026-09-01");
    expect(days[days.length - 1]).toBe("2026-09-30");
    expect(days).toHaveLength(30);
  });

  it("febrero bisiesto cierra en 29", () => {
    const days = buildMonthGrid("2028-02").flat().filter((d) => d.inMonth);
    expect(days[days.length - 1].iso).toBe("2028-02-29");
  });

  it("un mes que empieza lunes no arrastra semana previa", () => {
    // 2026-06-01 es lunes.
    expect(buildMonthGrid("2026-06")[0][0]).toEqual({
      iso: "2026-06-01",
      inMonth: true,
    });
  });
});

describe("shiftMonth", () => {
  it("retrocede cruzando el año", () => {
    expect(shiftMonth("2026-01", -1)).toBe("2025-12");
  });
  it("avanza cruzando el año", () => {
    expect(shiftMonth("2026-12", 1)).toBe("2027-01");
  });
});

describe("monthLabelEs", () => {
  it("rotula el mes en español, capitalizado y compacto", () => {
    // es-CO emite "septiembre de 2026"; el "de" no cabe en la sidebar.
    expect(monthLabelEs("2026-09")).toBe("Septiembre 2026");
  });
});

describe("nextRange (click sobre un día)", () => {
  it("el primer click ancla el inicio y deja el rango abierto", () => {
    expect(nextRange({ from: null, to: null }, "2026-09-08")).toEqual({
      from: "2026-09-08",
      to: null,
    });
  });

  it("el segundo click posterior cierra el rango", () => {
    expect(nextRange({ from: "2026-09-08", to: null }, "2026-09-10")).toEqual({
      from: "2026-09-08",
      to: "2026-09-10",
    });
  });

  it("el segundo click ANTERIOR invierte el rango en vez de romperlo", () => {
    expect(nextRange({ from: "2026-09-10", to: null }, "2026-09-08")).toEqual({
      from: "2026-09-08",
      to: "2026-09-10",
    });
  });

  it("clickear el mismo día lo cierra como rango de un día", () => {
    expect(nextRange({ from: "2026-09-08", to: null }, "2026-09-08")).toEqual({
      from: "2026-09-08",
      to: "2026-09-08",
    });
  });

  it("con un rango ya cerrado, el siguiente click empieza uno nuevo", () => {
    expect(
      nextRange({ from: "2026-09-01", to: "2026-09-10" }, "2026-09-20"),
    ).toEqual({ from: "2026-09-20", to: null });
  });
});
