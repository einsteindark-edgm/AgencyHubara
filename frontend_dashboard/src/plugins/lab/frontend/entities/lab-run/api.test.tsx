import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import threadFixture from "./fixtures/thread.json";
import {
  useActiveRun,
  useCancelRun,
  useLabEstimate,
  useLabRuns,
  useLaunchRun,
  useRunThread,
  useTurnTrace,
} from "./api";
import { labKeys } from "./keys";

const fetchMock = vi.fn();
const RUN = "run-20260923-1041-ab12";
const SID = "wa_573001234567";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return { client, wrapper };
}

beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("queries del laboratorio: solo /api/lab/*", () => {
  it("lista las corridas", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ runs: [{ run_id: RUN, arms: ["A0"] }] }));
    const { wrapper } = setup();
    const { result } = renderHook(() => useLabRuns(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.runs[0].run_id).toBe(RUN);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/lab\/runs$/);
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("estima con los bots, las repeticiones y el banco elegidos", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ turns: 309, estimate_usd: 3.2, fits: true, arms: [], reps: 3 }));
    const { wrapper } = setup();
    const { result } = renderHook(() => useLabEstimate({ arms: ["A1", "B"], reps: 3, bench: "new" }, true), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/lab/estimate?arms=A1%2CB&reps=3&bench=new");
  });

  it("no estima mientras el panel está cerrado", () => {
    const { wrapper } = setup();
    renderHook(() => useLabEstimate({ arms: ["A1"], reps: 1, bench: "new" }, false), { wrapper });

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("lee la corrida activa", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ active: { phase: "running", run_id: RUN } }));
    const { wrapper } = setup();
    const { result } = renderHook(() => useActiveRun(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.active?.phase).toBe("running");
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/api\/lab\/runs\/active$/);
  });

  it("lee el hilo de una conversación del banco (y el episodio si se pide)", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(threadFixture));
    const { wrapper } = setup();
    const { result } = renderHook(() => useRunThread(RUN, SID, "ep_1"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(String(fetchMock.mock.calls[0][0])).toContain(`/api/lab/runs/${RUN}/conversations/${SID}?episode=ep_1`);
  });

  it("la traza de un turno manda el turn_key codificado (lleva / y :)", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ fidelity: "v2", arm: "A0", rep: 0, trace: {}, steps: [] }));
    const { wrapper } = setup();
    const { result } = renderHook(() => useTurnTrace({ run: RUN, sid: SID, turnKey: "run:019f/t:2", arm: "A0", rep: 0 }), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      `/api/lab/runs/${RUN}/conversations/${SID}/turns/trace?turn_key=run%3A019f%2Ft%3A2&arm=A0&rep=0`,
    );
  });
});

describe("lanzar y cancelar", () => {
  it("lanzar manda el formulario y refresca la corrida activa y la lista", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ run_id: RUN, workflow_id: "lab-launch", estimate: {} }, 202));
    const { client, wrapper } = setup();
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useLaunchRun(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({ arms: ["A1", "B"], reps: 1, bench: "new" });
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/lab\/runs$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ arms: ["A1", "B"], reps: 1, bench: "new" });
    expect(spy).toHaveBeenCalledWith({ queryKey: labKeys.active() });
    expect(spy).toHaveBeenCalledWith({ queryKey: labKeys.runs() });
  });

  it("cancelar pide la cancelación de la corrida activa", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ cancel_requested: true, run_id: RUN }, 202));
    const { wrapper } = setup();
    const { result } = renderHook(() => useCancelRun(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync();
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/lab\/runs\/active\/cancel$/);
    expect(init.method).toBe("POST");
  });
});
