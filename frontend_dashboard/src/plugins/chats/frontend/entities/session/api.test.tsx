/**
 * Test de integración del hook contra `apiClient`. Mockeamos `fetch` y
 * verificamos:
 *   - `useSessions` parsea con Zod y retorna sólo el array
 *   - `useSession(null)` queda disabled (no fetch)
 *   - `useSession("wa_123")` golpea el endpoint correcto
 *
 * Renderizamos vía `QueryClientProvider` aislado para que cada test parta
 * con un cache limpio.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useSession, useSessions } from "./api";

const fetchMock = vi.fn();

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("useSessions", () => {
  it("fetches and parses the list", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          sessions: [
            {
              session_id: "wa_1",
              phone_number: "1",
              tag: "INTERESADO",
              motivo: "x",
              active_agent_route: "ventas",
              phone_number_id: null,
              last_updated_timestamp: 100,
            },
          ],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const { result } = renderHook(() => useSessions(), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.data?.[0].session_id).toBe("wa_1");
  });
});

describe("useSession", () => {
  it("is disabled when id is null", () => {
    const { result } = renderHook(() => useSession(null), {
      wrapper: makeWrapper(),
    });
    expect(result.current.fetchStatus).toBe("idle");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("hits the correct endpoint when id is provided", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          session_id: "wa_42",
          phone_number: "42",
          tag: "INTERESADO",
          motivo: "x",
          memory_content: null,
          active_agent_route: "ventas",
          phone_number_id: null,
          status_history: [],
          messages: [],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const { result } = renderHook(() => useSession("wa_42"), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/dashboard/sessions/wa_42");
    expect(result.current.data?.session_id).toBe("wa_42");
  });
});
