import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import type { ProviderReport } from "@plugins/costs/frontend/entities/cost-report";
import { DEEPSEEK_REPORT_FIXTURE } from "@plugins/costs/frontend/entities/cost-report/fixtures";

import { ProviderCostCard } from "./ProviderCostCard";

const RANGE = { start: "2026-10-22", end: "2026-11-04" };
const PROVIDER = { id: "deepseek", label: "DeepSeek", connected: true };

const REPORT: ProviderReport = {
  provider: "deepseek", status: "ok", source: "metered", start: RANGE.start, end: RANGE.end,
  productionUsdMicros: 7_500_000, productionCopMicros: 24_000_000_000, otherUsdMicros: 1_250_000,
  daily: [{ date: "2026-10-22", usdMicros: 5_000_000 }, { date: "2026-10-23", usdMicros: 2_500_000 }],
  lines: [
    { key: "p", label: "Hubara Ventas", group: "Llave de API", currency: "USD", amountMicros: 7_000_000,
      usdMicros: 7_000_000, copMicros: 22_400_000_000, production: true, quantity: 900, unit: "solicitudes" },
    { key: "c", label: "Hubara Ventas", group: "Llave de API", currency: "CNY", amountMicros: 3_550_000,
      usdMicros: 500_000, copMicros: 1_600_000_000, production: true, quantity: null, unit: null },
    { key: "l", label: "Laboratorios", group: "Llave de API", currency: "USD", amountMicros: 1_250_000,
      usdMicros: 1_250_000, copMicros: 4_000_000_000, production: false, quantity: 300, unit: "solicitudes" },
  ],
  notes: ["Cifras de la página Usage de DeepSeek."], warnings: ["Una advertencia"], error: null,
  howToConnect: null, fxNote: null, fetchedAt: "2026-11-04T15:00:00+00:00", cached: false,
};

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const query = (over: Partial<{ data: ProviderReport; isPending: boolean; isError: boolean; error: unknown }>) => ({
  data: undefined, isPending: false, isError: false, error: null, refetch: vi.fn(), ...over,
});

describe("ProviderCostCard", () => {
  it("shows the production amount, its source and each key", () => {
    wrap(<ProviderCostCard provider={PROVIDER} query={query({ data: REPORT })} range={RANGE} />);

    expect(screen.getByTestId("costs-card-deepseek-usd")).toHaveTextContent("US$7,50");
    expect(screen.getByText("Medición del proveedor")).toBeInTheDocument();
    expect(screen.getAllByText("Hubara Ventas").length).toBeGreaterThan(0);
    expect(screen.getByText("¥3,55 CNY")).toBeInTheDocument();
    expect(screen.getByText(/900 solicitudes/)).toBeInTheDocument();
  });

  it("puts non-production keys apart with their own subtotal", () => {
    wrap(<ProviderCostCard provider={PROVIDER} query={query({ data: REPORT })} range={RANGE} />);

    const apart = screen.getByRole("group", { name: /Fuera de producción/ });
    expect(apart).toHaveTextContent("Laboratorios");
    expect(apart).toHaveTextContent("US$1,25");
  });

  it("draws one bar per day of the range", () => {
    wrap(<ProviderCostCard provider={PROVIDER} query={query({ data: REPORT })} range={RANGE} />);

    expect(screen.getAllByTestId("costs-day-bar")).toHaveLength(2);
  });

  it("shows warnings", () => {
    wrap(<ProviderCostCard provider={PROVIDER} query={query({ data: REPORT })} range={RANGE} />);
    expect(screen.getByText("Una advertencia")).toBeInTheDocument();
  });

  it("a provider without credentials says how to connect it, not $0", () => {
    const notConnected: ProviderReport = {
      ...REPORT, status: "not_connected", source: null, productionUsdMicros: null,
      productionCopMicros: null, otherUsdMicros: null, daily: [], lines: [], warnings: [],
      howToConnect: "Guarda OPENROUTER_MANAGEMENT_KEY", notes: ["Hoy US$0.007"],
    };
    wrap(<ProviderCostCard provider={PROVIDER} query={query({ data: notConnected })} range={RANGE} />);

    expect(screen.getByText("Sin conectar")).toBeInTheDocument();
    expect(screen.getByText("Guarda OPENROUTER_MANAGEMENT_KEY")).toBeInTheDocument();
    expect(screen.queryByTestId("costs-card-deepseek-usd")).not.toBeInTheDocument();
  });

  it("a provider error is shown with a retry", () => {
    const failed: ProviderReport = { ...REPORT, status: "error", error: "DeepSeek no respondió a tiempo.",
      productionUsdMicros: null, lines: [], daily: [] };
    const q = query({ data: failed });
    wrap(<ProviderCostCard provider={PROVIDER} query={q} range={RANGE} />);

    expect(screen.getByRole("alert")).toHaveTextContent("DeepSeek no respondió a tiempo.");
    expect(screen.getByRole("button", { name: /Reintentar|Actualizar/ })).toBeInTheDocument();
  });

  it("while loading it says it is asking the provider", () => {
    wrap(<ProviderCostCard provider={PROVIDER} query={query({ isPending: true })} range={RANGE} />);
    expect(screen.getByText(/Consultando a DeepSeek/)).toBeInTheDocument();
  });
});

describe("ProviderCostCard refresh", () => {
  it("«Actualizar» asks the provider again without the server cache", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify(DEEPSEEK_REPORT_FIXTURE), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    try {
      wrap(<ProviderCostCard provider={PROVIDER} query={query({ data: REPORT })} range={RANGE} />);
      fireEvent.click(screen.getByRole("button", { name: "Actualizar" }));

      await waitFor(() => expect(fetchMock).toHaveBeenCalled());
      expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toContain(
        "/api/costs/providers/deepseek?start=2026-10-22&end=2026-11-04&refresh=true",
      );
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
