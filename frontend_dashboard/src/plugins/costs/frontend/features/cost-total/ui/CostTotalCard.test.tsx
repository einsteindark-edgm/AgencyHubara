import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { ProviderReport, TotalEntry } from "@plugins/costs/frontend/entities/cost-report";

import { CostTotalCard } from "./CostTotalCard";

function ok(id: string, usd: number, cop: number): ProviderReport {
  return {
    provider: id, status: "ok", source: "metered", start: "2026-10-22", end: "2026-11-04",
    productionUsdMicros: usd, productionCopMicros: cop, otherUsdMicros: null, daily: [],
    lines: [], notes: [], warnings: [], error: null, howToConnect: null,
    fxNote: "TRM oficial", fetchedAt: null, cached: false,
  };
}

describe("CostTotalCard", () => {
  it("shows the production total in dollars and pesos", () => {
    const entries: TotalEntry[] = [
      { id: "deepseek", label: "DeepSeek", report: ok("deepseek", 7_500_000, 24_000_000_000), state: "done" },
      { id: "whatsapp", label: "WhatsApp (Meta)", report: ok("whatsapp", 1_000_000, 3_200_000_000), state: "done" },
    ];
    render(<CostTotalCard entries={entries} range={{ start: "2026-10-22", end: "2026-11-04" }} />);

    expect(screen.getByTestId("costs-total-usd")).toHaveTextContent("US$8,50");
    expect(screen.getByTestId("costs-total-cop")).toHaveTextContent("$27.200 COP");
    expect(screen.queryByText(/Parcial/)).not.toBeInTheDocument();
  });

  it("marks the total partial and names what is missing", () => {
    const entries: TotalEntry[] = [
      { id: "deepseek", label: "DeepSeek", report: ok("deepseek", 7_500_000, 24_000_000_000), state: "done" },
      { id: "gemini", label: "Gemini (Google Cloud)", report: undefined, state: "loading" },
    ];
    render(<CostTotalCard entries={entries} range={{ start: "2026-10-22", end: "2026-11-04" }} />);

    expect(screen.getByText(/Parcial/)).toBeInTheDocument();
    expect(screen.getByText(/Gemini \(Google Cloud\): consultando/)).toBeInTheDocument();
  });
});
