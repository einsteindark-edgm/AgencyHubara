/**
 * Tests de las mutaciones de handoff. Mockeamos `fetch` y verificamos:
 *   - Cada hook llama al endpoint correcto con el body esperado.
 *   - El schema Zod parsea respuestas válidas.
 *   - El cache de `sessionKeys` se invalida en `onSuccess`.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { sessionKeys } from "@/entities/session";
import {
  useInterveneMutation,
  useReturnToBotMutation,
  useSendHumanMessageMutation,
} from "./api";

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

describe("useInterveneMutation", () => {
  it("posts to /intervene and invalidates session detail + list", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          active_route: "humano",
          tag: "HUMANO",
          motivo: "tomé control",
          terminated_workflows: ["session-wa_X"],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const { client, wrapper } = makeWrapper();
    const invalidate = vi.spyOn(client, "invalidateQueries");

    const { result } = renderHook(() => useInterveneMutation("wa_X"), {
      wrapper,
    });

    await act(async () => {
      await result.current.mutateAsync({ motivo: "tomé control" });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/dashboard/sessions/wa_X/intervene");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ motivo: "tomé control" });

    expect(result.current.data?.active_route).toBe("humano");
    expect(result.current.data?.terminated_workflows).toEqual(["session-wa_X"]);

    // Invalida tanto detail como list.
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: sessionKeys.detail("wa_X"),
    });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: sessionKeys.list(),
    });
  });

  it("throws when no session is selected", async () => {
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useInterveneMutation(null), {
      wrapper,
    });
    await expect(result.current.mutateAsync({})).rejects.toThrow(
      "No session selected",
    );
  });
});

describe("useSendHumanMessageMutation", () => {
  it("posts text and parses HumanMessageResponse", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          role: "assistant",
          sender: "human",
          content: "hola desde el dashboard",
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useSendHumanMessageMutation("wa_Y"), {
      wrapper,
    });

    let data: { sender: string } | undefined;
    await act(async () => {
      data = await result.current.mutateAsync({
        text: "hola desde el dashboard",
      });
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/dashboard/sessions/wa_Y/messages");
    expect(JSON.parse(init.body)).toEqual({ text: "hola desde el dashboard" });
    expect(data?.sender).toBe("human");
  });

  it("propagates ApiError on 409 (sesión no está en humano)", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ detail: "no está en ruta humano" }),
        { status: 409, headers: { "content-type": "application/json" } },
      ),
    );

    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useSendHumanMessageMutation("wa_Z"), {
      wrapper,
    });

    await expect(
      result.current.mutateAsync({ text: "intento" }),
    ).rejects.toBeTruthy();
  });
});

describe("useReturnToBotMutation", () => {
  it("posts target_route=ventas without motivo", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          active_route: "ventas",
          tag: "RETOMA_VENTA",
          motivo: "default",
          terminated_workflows: [],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useReturnToBotMutation("wa_A"), {
      wrapper,
    });

    let data: { tag: string } | undefined;
    await act(async () => {
      data = await result.current.mutateAsync({ target_route: "ventas" });
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/dashboard/sessions/wa_A/return-to-bot");
    expect(JSON.parse(init.body)).toEqual({ target_route: "ventas" });
    expect(data?.tag).toBe("RETOMA_VENTA");
  });

  it("posts target_route=remarketing with motivo", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          active_route: "remarketing",
          tag: "REMARKETING",
          motivo: "indeciso, retomar suave",
          terminated_workflows: [],
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useReturnToBotMutation("wa_B"), {
      wrapper,
    });

    let data: { active_route: string } | undefined;
    await act(async () => {
      data = await result.current.mutateAsync({
        target_route: "remarketing",
        motivo: "indeciso, retomar suave",
      });
    });

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      target_route: "remarketing",
      motivo: "indeciso, retomar suave",
    });
    expect(data?.active_route).toBe("remarketing");
  });
});
