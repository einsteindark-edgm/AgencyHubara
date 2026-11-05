/**
 * Hooks de Costos contra una QueryClient REAL (se stubea solo `fetch`): qué
 * URL piden, qué devuelven ya pasado por Zod, y que «Actualizar» pide sin
 * caché y deja la respuesta en la tarjeta.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import { useCostCatalog, useProviderReports, useRefreshProviderReport } from "./api";
import { CATALOG_FIXTURE, DEEPSEEK_REPORT_FIXTURE, NOT_CONNECTED_FIXTURE } from "./fixtures";
import { costKeys } from "./keys";
import type { ProviderReport } from "./model";

const fetchMock = vi.fn();
const RANGE = { start: "2026-10-22", end: "2026-11-04" };

function json(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }),
  );
}

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { qc, wrapper };
}

const calledUrls = () => fetchMock.mock.calls.map((c) => String(c[0]));

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("cost-report hooks", () => {
  it("loads the catalog with the default range", async () => {
    fetchMock.mockImplementation(() => json(CATALOG_FIXTURE));
    const { wrapper } = setup();
    const { result } = renderHook(() => useCostCatalog(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(calledUrls()[0]).toContain("/api/costs/providers");
    expect(result.current.data?.defaultStart).toBe("2026-10-22");
    expect(result.current.data?.providers[1].connected).toBe(false);
  });

  it("asks every provider for the range in parallel", async () => {
    fetchMock.mockImplementation((url: string) =>
      json(String(url).includes("/openrouter") ? NOT_CONNECTED_FIXTURE : DEEPSEEK_REPORT_FIXTURE),
    );
    const { wrapper } = setup();
    const { result } = renderHook(() => useProviderReports(["deepseek", "openrouter"], RANGE), { wrapper });

    await waitFor(() => expect(result.current.every((q) => q.isSuccess)).toBe(true));
    expect(calledUrls()).toEqual(
      expect.arrayContaining([
        expect.stringContaining("/api/costs/providers/deepseek?start=2026-10-22&end=2026-11-04"),
        expect.stringContaining("/api/costs/providers/openrouter?start=2026-10-22&end=2026-11-04"),
      ]),
    );
    expect(result.current[0].data?.productionUsdMicros).toBe(7_500_000);
    expect(result.current[1].data?.status).toBe("not_connected");
  });

  it("does not ask anything until there is a range", () => {
    const { wrapper } = setup();
    renderHook(() => useProviderReports(["deepseek"], null), { wrapper });

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refresh skips the server cache and updates that card", async () => {
    fetchMock.mockImplementation(() => json({ ...DEEPSEEK_REPORT_FIXTURE, cached: false }));
    const { qc, wrapper } = setup();
    const { result } = renderHook(() => useRefreshProviderReport("deepseek", RANGE), { wrapper });

    await act(async () => {
      await result.current.mutateAsync();
    });
    expect(calledUrls()[0]).toContain("refresh=true");
    const cached = qc.getQueryData<ProviderReport>(costKeys.report("deepseek", RANGE));
    expect(cached?.productionUsdMicros).toBe(7_500_000);
  });
});
