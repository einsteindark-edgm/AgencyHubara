import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import type { ReactNode } from "react";
import { useAgents } from "./api";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

const BASE_DTO = {
  id: "chats:sales",
  plugin_id: "chats",
  worker_name: "sales",
  name: "Sales Agent",
  role: "Ventas",
  workspace: {
    identity: "# Sales\nAyudo a vender.",
    soul: "",
    tools: "",
    agents: "",
    users: "",
    skills: [],
  },
};

function mockAgentsResponse(dtos: unknown[]) {
  fetchMock.mockResolvedValueOnce(
    new Response(JSON.stringify(dtos), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
}

function wrap({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return createElement(QueryClientProvider, { client: qc }, children);
}

describe("useAgents", () => {
  it("fetches and maps with Zod", async () => {
    mockAgentsResponse([BASE_DTO]);
    const { result } = renderHook(() => useAgents(), { wrapper: wrap });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const agents = result.current.data!;
    expect(agents).toHaveLength(1);
    expect(agents[0].id).toBe("chats:sales");
    expect(agents[0].plugin_id).toBe("chats");
    expect(agents[0].worker_name).toBe("sales");
    expect(agents[0].workspace.identity).toBe("# Sales\nAyudo a vender.");
  });

  it("provides default icon/color for known workers", async () => {
    mockAgentsResponse([BASE_DTO, { ...BASE_DTO, id: "chats:remarketing", worker_name: "remarketing" }]);
    const { result } = renderHook(() => useAgents(), { wrapper: wrap });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const agents = result.current.data!;
    expect(agents[0].icon).toBe("bolt");
    expect(agents[0].color).toBe("blue");
    expect(agents[1].icon).toBe("refresh");
    expect(agents[1].color).toBe("orange");
  });

  it("falls back to bot/blue for unknown worker_name", async () => {
    mockAgentsResponse([{ ...BASE_DTO, worker_name: "unknown_worker" }]);
    const { result } = renderHook(() => useAgents(), { wrapper: wrap });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const agents = result.current.data!;
    expect(agents[0].icon).toBe("bot");
    expect(agents[0].color).toBe("blue");
  });

  it("maps calls and csat to null", async () => {
    mockAgentsResponse([BASE_DTO]);
    const { result } = renderHook(() => useAgents(), { wrapper: wrap });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const agents = result.current.data!;
    expect(agents[0].calls).toBeNull();
    expect(agents[0].csat).toBeNull();
  });
});
