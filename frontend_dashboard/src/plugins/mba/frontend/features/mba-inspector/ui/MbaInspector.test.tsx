import { describe, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { MbaInspector } from "./MbaInspector";
import { MBA_CONFIG_FIXTURE } from "@plugins/mba/frontend/entities/mba-config/fixture";

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

describe("MbaInspector", () => {
  it("shows the state in Meta: entity, rollout, audience, requests and problems", async () => {
    fetchMock.mockResolvedValue(jsonResponse(MBA_CONFIG_FIXTURE));
    renderWithClient(<MbaInspector agentId="sales" />);
    await waitFor(() => screen.getByText("Estado en Meta"));
    screen.getByText("sin onboardear");
    screen.getByText("ALLOWLISTED_ONLY");
    screen.getByText("1 teléfono(s)");
    screen.getByText("12");
  });

  it("shows a visible error instead of vanishing when the backend fails", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "boom" }, 500));
    renderWithClient(<MbaInspector agentId="sales" />);
    await waitFor(() => screen.getByText(/No se pudo cargar/));
  });
});
