import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { scorecardKeys } from "@plugins/agents_admin/frontend/entities/scorecard";

import createdFixture from "./fixtures/label-created.json";
import { useCreateLabel } from "./api";
import { evalLabelKeys } from "./keys";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("useCreateLabel", () => {
  it("postea la etiqueta e invalida etiquetas, cola y calibración", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const labelsKey = evalLabelKeys.labels("wa_570000000005", "ep_002");
    client.setQueryData(labelsKey, { labels: [] });
    client.setQueryData(evalLabelKeys.queue(30, 20), { items: [] });
    client.setQueryData(evalLabelKeys.calibration(), { checks: [] });
    client.setQueryData(scorecardKeys.list(30), { scorecards: [] });

    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(createdFixture), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const { result } = renderHook(() => useCreateLabel(), { wrapper });
    const input = {
      session_id: "wa_570000000005",
      episode_id: "ep_002",
      check_id: "DES-04",
      verdict: "pasa" as const,
      note: "Recomendó lo que pidió",
    };
    await act(() => result.current.mutateAsync(input));

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/agents/evals/labels");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual(input);
    expect(client.getQueryState(labelsKey)?.isInvalidated).toBe(true);
    expect(client.getQueryState(evalLabelKeys.queue(30, 20))?.isInvalidated).toBe(true);
    expect(client.getQueryState(evalLabelKeys.calibration())?.isInvalidated).toBe(true);
    expect(client.getQueryState(scorecardKeys.list(30))?.isInvalidated).toBe(false);
  });
});
