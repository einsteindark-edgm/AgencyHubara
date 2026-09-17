/**
 * Las ventas atribuidas a una campaña salen del valor del pedido en Orders
 * (OrderFacts, pedido #31): si una orden cambia, las stats se refrescan; si
 * Orders no respondió, el backend lo avisa con `orders_stale`.
 */
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";

const handlers = vi.hoisted(() => ({} as Record<string, (e: unknown) => void>));

vi.mock("@/shared/api", () => ({
  useDashboardEvents: (domain: string, handler: (e: unknown) => void) => {
    handlers[domain] = handler;
  },
  useInvalidateOnReconnect: () => {},
}));

import { mapBackendStats, useCampaignOrdersEvents } from "./api";
import { backendCampaignStatsSchema } from "./contracts";

describe("stats ↔ OrderFacts", () => {
  it("mapea orders_stale (backend viejo → false)", () => {
    const base = { campaign_id: "mkt-1" };
    expect(mapBackendStats(backendCampaignStatsSchema.parse(base)).ordersStale).toBe(false);
    expect(
      mapBackendStats(backendCampaignStatsSchema.parse({ ...base, orders_stale: true })).ordersStale,
    ).toBe(true);
  });

  it("un evento de orders invalida las stats de campañas", () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    renderHook(() => useCampaignOrdersEvents(), {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      ),
    });
    handlers.orders({ domain: "orders", type: "changed" });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["marketing-campaign", "stats"] });
  });
});
