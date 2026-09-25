/**
 * El seed del "Analizar con IA" se pide POR CAMPAÑA y con la ventana del header
 * (caso Halloween 2026-09-25: el análisis mezclaba toda la cuenta de los últimos
 * 14 días aunque el operador estuviera mirando una campaña y otro rango).
 */
import { describe, expect, it } from "vitest";

import { analysisInputPath } from "./model";

describe("analysisInputPath", () => {
  it("con campaña y rango custom → campaign_id + from/to", () => {
    expect(analysisInputPath({ campaignId: "C1", days: null, from: "2026-09-11", to: "2026-09-25" }))
      .toBe("/api/ads/meta/analysis-input?campaign_id=C1&from=2026-09-11&to=2026-09-25");
  });

  it("con campaña y preset → campaign_id + days", () => {
    expect(analysisInputPath({ campaignId: "C1", days: 30, from: null, to: null }))
      .toBe("/api/ads/meta/analysis-input?campaign_id=C1&days=30");
  });

  it("sin campaña ni ventana → los 14 días de siempre (cuenta completa)", () => {
    expect(analysisInputPath({})).toBe("/api/ads/meta/analysis-input?days=14");
  });

  it("preset 'Total' (sin days) se acota a 90 días", () => {
    expect(analysisInputPath({ campaignId: "C1", days: null, from: null, to: null }))
      .toBe("/api/ads/meta/analysis-input?campaign_id=C1&days=90");
  });
});
