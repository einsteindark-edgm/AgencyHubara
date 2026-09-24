import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { RunLauncher } from "./RunLauncher";

/**
 * Si la caja no prendió o nunca reportó, la corrida no deja progreso en S3:
 * el panel muestra cómo terminó la última (antes desaparecía y el operador
 * relanzaba a ciegas).
 */
const fetchMock = vi.fn();

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((url: string) =>
    String(url).includes("/api/lab/runs/active")
      ? json({ active: null, last: { phase: "failed", run_id: "run-20260923-a1b2", error: "la caja no prendió" } })
      : json({ bench_id: null, turns: 10, estimate_usd: 1, fits: true }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("RunLauncher: la última corrida", () => {
  it("dice cómo terminó la última corrida que falló", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <RunLauncher lastBenchId={null} />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Nueva corrida" }));

    expect(await screen.findByText(/La última corrida \(run-20260923-a1b2\) falló: la caja no prendió/)).toBeInTheDocument();
  });
});
