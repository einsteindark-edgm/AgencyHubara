import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { MbaAgentCanvas } from "./MbaAgentCanvas";
import { MBA_CONFIG_FIXTURE } from "@plugins/mba/frontend/entities/mba-config/fixture";

const fetchMock = vi.fn();

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const SALES = {
  id: "sales",
  display_name: "Asesor de Ventas",
  role: "Ventas premium por WhatsApp",
  channel: "whatsapp",
  icon: "bot",
  color: "violet",
  entity_id: null,
};

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("MbaAgentCanvas", () => {
  it("renders the agent header, the Configuración tab and the future tabs disabled", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/mba/agents")) return Promise.resolve(jsonResponse({ agents: [SALES] }));
      return Promise.resolve(jsonResponse(MBA_CONFIG_FIXTURE));
    });

    renderWithClient(<MbaAgentCanvas agentId="sales" />);

    await waitFor(() => screen.getByRole("heading", { name: "Asesor de Ventas" }));
    expect(screen.getByRole("button", { name: /Configuración/ }).className).toContain("on");
    for (const label of ["Insights", "Agent test", "Agent eval"]) {
      const btn = screen.getByRole("button", { name: new RegExp(label) }) as HTMLButtonElement;
      expect(btn.disabled).toBe(true);
      expect(btn.title).toBe("Próximamente");
    }
    // y monta la configuración real desde /api/mba/agents/sales/config
    await waitFor(() => screen.getByText("Secuencia de envío"));
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/api/mba/agents/sales/config"))).toBe(true);
  });

  it("shows an empty state when there are no MBA agents", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ agents: [] }));
    renderWithClient(<MbaAgentCanvas agentId="sales" />);
    await waitFor(() => screen.getByText(/No hay agentes MBA/));
  });
});
