/**
 * Pospuesto MANUAL desde el inspector: `POST /postpone` con {date, note} y
 * `DELETE /postpone` para quitarlo. Ambos invalidan la bandeja (la fila entra
 * o sale del filtro "Pospuestos") y el detalle.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { sessionKeys } from "@plugins/chats/frontend/entities/session";
import { useClearPostponeMutation, usePostponeMutation } from "./api";

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

beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

const POSTPONED = {
  status: "esperando",
  kind: "manual",
  until_ms: 1_790_607_600_000,
  resume_label: "el lunes 28 de septiembre",
  text: "Llamar para cerrar",
  overdue: false,
};

describe("usePostponeMutation", () => {
  it("POSTea la fecha y la nota, e invalida bandeja + detalle", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ postponed: POSTPONED }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const { client, wrapper } = makeWrapper();
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => usePostponeMutation("wa_573001234567"), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({ date: "2026-09-28", note: "Llamar para cerrar" });
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/chats/session-actions/wa_573001234567/postpone");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ date: "2026-09-28", note: "Llamar para cerrar" });
    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith({ queryKey: sessionKeys.list() });
      expect(spy).toHaveBeenCalledWith({ queryKey: sessionKeys.detail("wa_573001234567") });
    });
  });
});

describe("useClearPostponeMutation", () => {
  it("DELETE quita el pospuesto e invalida la bandeja", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ postponed: null }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const { client, wrapper } = makeWrapper();
    const spy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useClearPostponeMutation("wa_573001234567"), { wrapper });

    await act(async () => {
      await result.current.mutateAsync();
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/chats/session-actions/wa_573001234567/postpone");
    expect(init.method).toBe("DELETE");
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: sessionKeys.list() }));
  });
});
