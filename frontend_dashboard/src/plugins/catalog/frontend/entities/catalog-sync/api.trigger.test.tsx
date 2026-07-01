/**
 * Test del wiring de `useTriggerSync` — el flag `force` en el body.
 *
 * El botón "Sincronizar" hace un sync DELTA (force=false). El checkbox "Forzar
 * re-sync completo" pasa `true` → el body lleva `force:true` → el backend corre
 * el push completo (recupera imágenes que Meta no fetcheó). Mockeamos `fetch` y
 * verificamos el body exacto en ambos casos.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useTriggerSync } from "./api";

const fetchMock = vi.fn();

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { wrapper };
}

function okResponse() {
  return new Response(
    JSON.stringify({
      workflow_id: "wf-1",
      run_id: "run-1",
      started_at_ms: 1779800400000,
      status: "running",
      already_running: false,
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockResolvedValue(okResponse());
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("useTriggerSync", () => {
  it("botón normal (sin arg) → body { force: false }", async () => {
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useTriggerSync(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync();
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/catalog/sync");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ force: false });
  });

  it("checkbox de recuperación (true) → body { force: true }", async () => {
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useTriggerSync(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync(true);
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ force: true });
  });
});
