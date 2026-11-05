import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import type { ProviderReport, TotalEntry } from "@plugins/costs/frontend/entities/cost-report";

import { EXCLUDED_STORAGE_KEY } from "../model/selection";
import { CostTotalCard } from "./CostTotalCard";

const RANGE = { start: "2026-10-22", end: "2026-11-04" };

function ok(id: string, usd: number, cop: number): ProviderReport {
  return {
    provider: id, status: "ok", source: "metered", start: "2026-10-22", end: "2026-11-04",
    productionUsdMicros: usd, productionCopMicros: cop, otherUsdMicros: null, daily: [],
    lines: [], notes: [], warnings: [], error: null, howToConnect: null,
    fxNote: "TRM oficial", fetchedAt: null, cached: false,
  };
}

const THREE: TotalEntry[] = [
  { id: "deepseek", label: "DeepSeek", report: ok("deepseek", 7_500_000, 24_000_000_000), state: "done" },
  { id: "openrouter", label: "Jev (OpenRouter)", report: ok("openrouter", 500_000, 1_600_000_000), state: "done" },
  { id: "whatsapp", label: "WhatsApp (Meta)", report: ok("whatsapp", 1_000_000, 3_200_000_000), state: "done" },
];

beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

describe("CostTotalCard", () => {
  it("shows the production total in dollars and pesos", () => {
    render(<CostTotalCard entries={THREE.slice(0, 1).concat(THREE[2])} range={RANGE} />);

    expect(screen.getByTestId("costs-total-usd")).toHaveTextContent("US$8,50");
    expect(screen.getByTestId("costs-total-cop")).toHaveTextContent("$27.200 COP");
    expect(screen.queryByText(/Parcial/)).not.toBeInTheDocument();
  });

  it("marks the total partial and names what is missing", () => {
    const entries: TotalEntry[] = [
      THREE[0],
      { id: "gemini", label: "Gemini (Google Cloud)", report: undefined, state: "loading" },
    ];
    render(<CostTotalCard entries={entries} range={RANGE} />);

    expect(screen.getByText(/Parcial/)).toBeInTheDocument();
    expect(screen.getByText(/Gemini \(Google Cloud\): consultando/)).toBeInTheDocument();
  });
});

describe("CostTotalCard selection", () => {
  it("has one checkbox per provider, all checked at first", () => {
    render(<CostTotalCard entries={THREE} range={RANGE} />);

    const boxes = screen.getAllByRole("checkbox");
    expect(boxes).toHaveLength(3);
    expect(boxes.every((b) => (b as HTMLInputElement).checked)).toBe(true);
  });

  it("unchecking a provider takes it out of the total", () => {
    render(<CostTotalCard entries={THREE} range={RANGE} />);

    fireEvent.click(screen.getByRole("checkbox", { name: /WhatsApp/ }));

    expect(screen.getByTestId("costs-total-usd")).toHaveTextContent("US$8,00");
    expect(screen.getByTestId("costs-total-cop")).toHaveTextContent("$25.600 COP");
    expect(screen.getByRole("heading", { name: /seleccionado/ })).toBeInTheDocument();
  });

  it("only the checked ones count: deepseek, jev and gemini", () => {
    const entries: TotalEntry[] = [
      ...THREE,
      { id: "gemini", label: "Gemini (fotos y audios)", report: ok("gemini", 250_000, 800_000_000), state: "done" },
      { id: "temporal", label: "Temporal Cloud", report: ok("temporal", 6_000_000, 19_200_000_000), state: "done" },
    ];
    render(<CostTotalCard entries={entries} range={RANGE} />);

    fireEvent.click(screen.getByRole("checkbox", { name: /WhatsApp/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Temporal/ }));

    expect(screen.getByTestId("costs-total-usd")).toHaveTextContent("US$8,25");
  });

  it("an unchecked provider that is missing does not make the total partial", () => {
    const entries: TotalEntry[] = [
      THREE[0],
      { id: "temporal", label: "Temporal Cloud", report: undefined, state: "failed" },
    ];
    render(<CostTotalCard entries={entries} range={RANGE} />);

    fireEvent.click(screen.getByRole("checkbox", { name: /Temporal/ }));

    expect(screen.queryByText(/Parcial/)).not.toBeInTheDocument();
  });

  it("«Marcar todos» puts every provider back", () => {
    render(<CostTotalCard entries={THREE} range={RANGE} />);
    fireEvent.click(screen.getByRole("checkbox", { name: /WhatsApp/ }));

    fireEvent.click(screen.getByRole("button", { name: "Marcar todos" }));

    expect(screen.getByTestId("costs-total-usd")).toHaveTextContent("US$9,00");
  });

  it("the choice is remembered in this browser", () => {
    const { unmount } = render(<CostTotalCard entries={THREE} range={RANGE} />);
    fireEvent.click(screen.getByRole("checkbox", { name: /Jev/ }));
    unmount();

    render(<CostTotalCard entries={THREE} range={RANGE} />);

    expect(screen.getByRole("checkbox", { name: /Jev/ })).not.toBeChecked();
    expect(JSON.parse(localStorage.getItem(EXCLUDED_STORAGE_KEY) ?? "[]")).toEqual(["openrouter"]);
  });
});
