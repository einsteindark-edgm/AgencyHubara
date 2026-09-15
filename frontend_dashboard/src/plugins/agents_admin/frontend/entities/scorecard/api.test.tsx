import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { checkStatsKeys } from "@plugins/agents_admin/frontend/entities/check-stats";

import detailFixture from "./fixtures/scorecard-detail.json";
import listFixture from "./fixtures/scorecards.json";
import { useRescoreScorecard, useScorecard, useScorecards } from "./api";
import { scorecardKeys } from "./keys";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("queries del scorecard", () => {
  it("lista los scorecards de la ventana pedida", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(listFixture));
    const { wrapper } = setup();
    const { result } = renderHook(() => useScorecards(30), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.scorecards[0].verdict).toBe("FALLA");
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/agents/evals/scorecards?days=30");
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("el detalle no dispara sin sesión seleccionada", () => {
    const { wrapper } = setup();
    renderHook(() => useScorecard(null, ""), { wrapper });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("useRescoreScorecard", () => {
  it("postea el recálculo, siembra el detalle e invalida lista, detalle y agregados", async () => {
    const { client, wrapper } = setup();
    const detailKey = scorecardKeys.detail("wa_570000000001", "ep_007");
    client.setQueryData(scorecardKeys.list(30), { scorecards: [] });
    client.setQueryData(detailKey, { stored: false, scorecard: null });
    client.setQueryData(checkStatsKeys.detail(56), { episodes: 0 });
    client.setQueryData(scorecardKeys.registry(), { checks: [] });

    fetchMock.mockResolvedValueOnce(jsonResponse(detailFixture));
    const { result } = renderHook(() => useRescoreScorecard(), { wrapper });
    await act(() =>
      result.current.mutateAsync({
        session_id: "wa_570000000001",
        episode_id: "ep_007",
        judge: true,
      }),
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/agents/evals/scorecard/rescore");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      session_id: "wa_570000000001",
      episode_id: "ep_007",
      judge: true,
    });

    expect(client.getQueryData<{ stored: boolean }>(detailKey)?.stored).toBe(true);
    expect(client.getQueryState(scorecardKeys.list(30))?.isInvalidated).toBe(true);
    expect(client.getQueryState(detailKey)?.isInvalidated).toBe(true);
    expect(client.getQueryState(checkStatsKeys.detail(56))?.isInvalidated).toBe(true);
    // El registro no depende del recálculo.
    expect(client.getQueryState(scorecardKeys.registry())?.isInvalidated).toBe(false);
  });
});
