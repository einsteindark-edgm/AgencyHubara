import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { LabRun } from "@plugins/lab/frontend/entities/lab-run";

import { BenchRuns } from "./BenchRuns";

/**
 * Una corrida que dejó de reportar no queda "Corriendo" para siempre; un 5xx
 * no se confunde con "todavía no publicó".
 */
const fetchMock = vi.fn();

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

function run(over: Partial<LabRun>): LabRun {
  return {
    run_id: "run-20260923-a1b2", bench_id: "bench-x", arms: ["A1"], reps: 1, registry_version: 4, counts: {},
    phase: "simulating", turns_done: 3, turns_total: 10, spent_usd: 1, error: null, notes: [],
    started_at_ms: 1_790_200_000_000, updated_at_ms: 1_790_200_000_000, stale: false, ...over,
  };
}

function renderIt(runs: LabRun[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <BenchRuns runs={runs} selectedRun={runs[0].run_id} onSelectRun={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("BenchRuns: estados raros", () => {
  it("una corrida sin reportes dice que la caja dejó de reportar", async () => {
    fetchMock.mockImplementation(() => json({ detail: "no" }, 404));
    renderIt([run({ stale: true })]);

    expect(await screen.findByText(/sin reportes de la caja/i)).toBeInTheDocument();
  });

  it("un error del servidor no se muestra como 'todavía no publicó'", async () => {
    fetchMock.mockImplementation(() => json({ detail: "boom" }, 500));
    renderIt([run({ phase: "done" })]);

    expect(await screen.findByText(/no se pudo leer el banco/i)).toBeInTheDocument();
    expect(screen.queryByText(/todavía no publicó/i)).not.toBeInTheDocument();
  });
});
