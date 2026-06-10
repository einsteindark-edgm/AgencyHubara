import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useAgents } from "./api";

const fetchMock = vi.fn();

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

const VALID_AGENT = {
  id: "sales",
  name: "Asesor de Ventas",
  role: "Ventas premium por WhatsApp",
  model: "DeepSeek V4 Pro",
  category: "Ventas",
  icon: "bolt",
  color: "blue",
  workspace: "hubara_agency/src/plugins/chats/agent/sales/workspace",
  capabilities: [{ label: "Buscar en el catálogo", icon: "notes" }],
  prompts: [
    {
      key: "identity",
      filename: "IDENTITY.md",
      content: "# Eres el Asesor Exclusivo de Ventas de Hubara",
      word_count: 8,
    },
  ],
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("useAgents", () => {
  it("hits /api/agents and returns the parsed agent list with real prompt content", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ agents: [VALID_AGENT] }));

    const { result } = renderHook(() => useAgents(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.data?.[0].id).toBe("sales");
    expect(result.current.data?.[0].prompts[0].content).toContain(
      "Asesor Exclusivo de Ventas",
    );

    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/agents");
  });

  it("rejects a payload that violates the Zod contract (unknown color)", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ agents: [{ ...VALID_AGENT, color: "chartreuse" }] }),
    );

    const { result } = renderHook(() => useAgents(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
