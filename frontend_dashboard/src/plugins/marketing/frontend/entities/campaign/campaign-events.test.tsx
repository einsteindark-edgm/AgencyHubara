/**
 * Avance en vivo: el worker escribe el avance en el vault, la API lo publica
 * en el stream del dashboard (`marketing / campaign_updated`) y la lista se
 * refresca al recibirlo — sin esperar el refetch de 60 s. Una reconexión del
 * stream también la refresca (pudo perderse un evento en el corte).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

const stream = vi.hoisted(() => ({
  handlers: {} as Record<string, (e: unknown) => void>,
  onReconnect: null as null | (() => void),
}));
const get = vi.hoisted(() => vi.fn());

vi.mock("@/shared/api", () => ({
  useDashboardEvents: (domain: string, handler: (e: unknown) => void) => {
    stream.handlers[domain] = handler;
  },
  useInvalidateOnReconnect: (invalidate: () => void) => {
    stream.onReconnect = invalidate;
  },
}));

vi.mock("@/shared/sdk", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  apiClient: { get },
}));

import { useCampaignEvents, useCampaigns } from "./api";

function sending(processed: number) {
  return {
    campaigns: [
      {
        id: "mkt-1",
        name: "Halloween",
        status: "sending",
        progress: { total: 500, processed, sent: processed, state: "sending" },
      },
    ],
  };
}

function renderLiveList() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderHook(
    () => {
      useCampaignEvents();
      return useCampaigns();
    },
    {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      ),
    },
  );
}

describe("avance de la campaña en vivo", () => {
  beforeEach(() => {
    get.mockReset();
    stream.handlers = {};
    stream.onReconnect = null;
  });

  it("un evento de marketing trae el avance nuevo", async () => {
    get.mockResolvedValueOnce(sending(100)).mockResolvedValueOnce(sending(200));
    const { result } = renderLiveList();
    await waitFor(() => expect(result.current.data?.[0]?.progress?.processed).toBe(100));

    act(() => {
      stream.handlers.marketing?.({ domain: "marketing", type: "campaign_updated", id: "mkt-1" });
    });

    await waitFor(() => expect(result.current.data?.[0]?.progress?.processed).toBe(200));
  });

  it("al reconectar el stream también se refresca", async () => {
    get.mockResolvedValueOnce(sending(100)).mockResolvedValueOnce(sending(300));
    const { result } = renderLiveList();
    await waitFor(() => expect(result.current.data?.[0]?.progress?.processed).toBe(100));

    act(() => {
      stream.onReconnect?.();
    });

    await waitFor(() => expect(result.current.data?.[0]?.progress?.processed).toBe(300));
  });
});
