import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AgentsInspector } from "./AgentsInspector";

/** El inspector recibe paneles extra que compone la página (p. ej. el encendido del bot nuevo en ventas). */

const fetchMock = vi.fn();
const SALES = {
  id: "sales",
  name: "Asesor de Ventas",
  role: "Ventas por WhatsApp",
  model: "DeepSeek",
  category: "Ventas",
  icon: "bolt",
  color: "blue",
  workspace: "hubara_agency/src/plugins/chats/agent/sales/workspace",
  capabilities: [],
  prompts: [],
};

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation(() =>
    Promise.resolve(
      new Response(
        JSON.stringify({ agents: [SALES] }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("AgentsInspector", () => {
  it("muestra los paneles extra que le pasa la página", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <AgentsInspector agentId="sales">
          <div>Panel extra</div>
        </AgentsInspector>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("Asesor de Ventas")).toBeInTheDocument();
    expect(screen.getByText("Panel extra")).toBeInTheDocument();
  });
});
