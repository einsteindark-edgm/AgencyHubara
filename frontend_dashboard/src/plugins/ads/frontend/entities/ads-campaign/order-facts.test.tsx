/**
 * Ads lee el valor de los pedidos de la misma fuente que Orders (OrderFacts,
 * pedido #31). En el front eso implica dos cosas:
 *   - cuando cambia una orden (evento `orders` del stream) Ads refetchea;
 *   - si el backend avisa `orders_stale`, la UI lo sabe para mostrar el aviso.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

const handlers: Record<string, (event: unknown) => void> = {};
const get = vi.fn();

vi.mock("@/shared/api", () => ({
  apiClient: { get: (...args: unknown[]) => get(...args) },
  ApiError: class extends Error {},
  useDashboardEvents: (domain: string, handler: (event: unknown) => void) => {
    handlers[domain] = handler;
  },
  useInvalidateOnReconnect: () => {},
}));

import { useAdsCampaigns, useAdsOrdersEvents, useAdsOrdersStale } from "./api";

const params = { days: 30, from: null, to: null };

function wrapper(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  get.mockReset();
  for (const k of Object.keys(handlers)) delete handlers[k];
});

describe("Ads ↔ OrderFacts", () => {
  it("expone orders_stale del backend", async () => {
    get.mockResolvedValue({ campaigns: [], orders_stale: true });
    const qc = new QueryClient();
    const { result } = renderHook(
      () => ({ campaigns: useAdsCampaigns(params), stale: useAdsOrdersStale(params) }),
      { wrapper: wrapper(qc) },
    );
    await waitFor(() => expect(result.current.stale).toBe(true));
    expect(result.current.campaigns.data).toEqual([]);
    expect(get).toHaveBeenCalledTimes(1); // ambos hooks comparten la query
  });

  it("backend viejo sin orders_stale → false", async () => {
    get.mockResolvedValue({ campaigns: [] });
    const qc = new QueryClient();
    const { result } = renderHook(() => useAdsOrdersStale(params), {
      wrapper: wrapper(qc),
    });
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(result.current).toBe(false);
  });

  it("un evento de orders invalida las queries de Ads", async () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    renderHook(() => useAdsOrdersEvents(), { wrapper: wrapper(qc) });
    expect(handlers.orders).toBeTypeOf("function");
    handlers.orders({ domain: "orders", type: "changed", id: "#31" });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["ads-campaign"] });
  });
});
