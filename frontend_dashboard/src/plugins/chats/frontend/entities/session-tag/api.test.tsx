/**
 * `useReassignTagMutation`: endpoint correcto + body + parse Zod + invalidación
 * del detalle y la lista (el tag vive en ambos).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { sessionKeys } from "@plugins/chats/frontend/entities/session";
import { useReassignTagMutation } from "./api";

const fetchMock = vi.fn();

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
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

describe("useReassignTagMutation", () => {
  it("posts to /operator-tag with tag+motivo and invalidates detail + list", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          tag: "RECHAZO",
          motivo: "buscaba cera, no la vendemos",
          active_route: "ventas",
          episode_closed: { episode_id: "ep_001", closing_tag: "RECHAZO" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const { client, wrapper } = makeWrapper();
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useReassignTagMutation("wa_573114842180"), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({ tag: "RECHAZO", motivo: "buscaba cera, no la vendemos" });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/chats/session-actions/wa_573114842180/operator-tag");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      tag: "RECHAZO",
      motivo: "buscaba cera, no la vendemos",
    });
    expect(result.current.data?.episode_closed?.closing_tag).toBe("RECHAZO");
    expect(spy).toHaveBeenCalledWith({ queryKey: sessionKeys.detail("wa_573114842180") });
    expect(spy).toHaveBeenCalledWith({ queryKey: sessionKeys.list() });
  });

  it("rejects without a session", async () => {
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useReassignTagMutation(null), { wrapper });
    await expect(
      act(() => result.current.mutateAsync({ tag: "INTERESADO", motivo: "x" })),
    ).rejects.toThrow(/No session/);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
