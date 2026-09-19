/**
 * `fmtUsdMicros` — costos de WhatsApp (USD micros = 1e-6 USD, enteros).
 *
 * Un mensaje de servicio en Colombia cuesta US$0.0008: con 2 decimales toda
 * conversación se leería "US$0.00". Por debajo de US$1 van 4 decimales; de
 * US$1 en adelante, 2 (acumulado de una campaña grande).
 */
import { describe, expect, it } from "vitest";

import { fmtUsdMicros } from "./format";

describe("fmtUsdMicros", () => {
  it("sub-dólar con 4 decimales (un mensaje de servicio se lee)", () => {
    expect(fmtUsdMicros(800)).toBe("US$0.0008");
    expect(fmtUsdMicros(22_900)).toBe("US$0.0229");
  });

  it("de US$1 en adelante, 2 decimales", () => {
    expect(fmtUsdMicros(3_274_500)).toBe("US$3.27");
  });

  it("cero es 'US$0'", () => {
    expect(fmtUsdMicros(0)).toBe("US$0");
  });
});
