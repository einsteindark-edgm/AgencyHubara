import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { MbaAgentsList } from "./MbaAgentsList";

const fetchMock = vi.fn();
function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}
function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}
beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("MbaAgentsList", () => {
  it("lists the MBA agents from /api/mba/agents and reports the selection", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ agents: [{ id: "sales", display_name: "Asesor de Ventas", role: "Ventas", channel: "whatsapp", icon: "bot", color: "violet", entity_id: null }] }),
    );
    const onSelect = vi.fn();
    renderWithClient(<MbaAgentsList selectedId="sales" onSelect={onSelect} />);
    await waitFor(() => screen.getByText("Asesor de Ventas"));
    fireEvent.click(screen.getByText("Asesor de Ventas"));
    expect(onSelect).toHaveBeenCalledWith("sales");
  });

  it("shows a visible error instead of an empty list when the backend fails", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "boom" }, 500));
    renderWithClient(<MbaAgentsList selectedId="sales" onSelect={() => {}} />);
    await waitFor(() => screen.getByText(/No se pudieron cargar/));
  });
});
