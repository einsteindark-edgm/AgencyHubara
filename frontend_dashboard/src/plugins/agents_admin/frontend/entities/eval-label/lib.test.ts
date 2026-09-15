import { describe, expect, it } from "vitest";

import { calibrationStatusLabel, formatKappa, formatRate, queueReasonLabel } from "./lib";

describe("etiquetas de calibración", () => {
  it("formatea tasas y kappa con guion cuando no hay dato", () => {
    expect(formatRate(0.8571)).toBe("86 %");
    expect(formatRate(null)).toBe("—");
    expect(formatKappa(0.7619)).toBe("0.76");
    expect(formatKappa(null)).toBe("—");
  });

  it("nombra estados y razones de la cola", () => {
    expect(calibrationStatusLabel("sin_datos")).toBe("sin datos");
    expect(calibrationStatusLabel("confiable")).toBe("confiable");
    expect(queueReasonLabel("desconocido")).toBe("el juez no decidió");
    expect(queueReasonLabel("falla")).toBe("el juez dijo falla");
    expect(queueReasonLabel("muestra")).toBe("muestra aleatoria");
    expect(queueReasonLabel("otra")).toBe("otra");
  });
});
