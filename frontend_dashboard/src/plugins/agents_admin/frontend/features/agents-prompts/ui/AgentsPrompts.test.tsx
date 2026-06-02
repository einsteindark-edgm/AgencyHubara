import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { AgentsPrompts } from "./AgentsPrompts";

const fetchMock = vi.fn();

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

const SALES_AGENT = {
  id: "sales",
  name: "Asesor de Ventas",
  role: "Ventas premium por WhatsApp",
  model: "DeepSeek V4 Pro",
  category: "Ventas",
  icon: "bolt",
  color: "blue",
  workspace: "hubara_agency/src/plugins/chats/agent/sales/workspace",
  capabilities: [],
  prompts: [
    { key: "agents", filename: "AGENTS.md", content: "Coordínate con otros agentes IA.", word_count: 4 },
    { key: "identity", filename: "IDENTITY.md", content: "Eres el Asesor Exclusivo de Ventas de Hubara.", word_count: 8 },
    { key: "soul", filename: "SOUL.md", content: "Tu propósito es vender con calidez.", word_count: 6 },
    { key: "tools", filename: "TOOLS.md", content: "Puedes buscar productos y registrar pedidos.", word_count: 6 },
    { key: "users", filename: "USER.md", content: "Tus clientes son colombianos por WhatsApp.", word_count: 6 },
  ],
};

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("AgentsPrompts", () => {
  it("renders the 5 real workspace prompts (content + filenames), without the mock version badge", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ agents: [SALES_AGENT] }));

    renderWithClient(<AgentsPrompts agentId="sales" />);

    // El header trae el nombre real del agente.
    await waitFor(() => screen.getByText("Asesor de Ventas"));

    // El CONTENIDO REAL de cada .md llega al DOM (getByText lanza si falta).
    screen.getByText("Eres el Asesor Exclusivo de Ventas de Hubara.");
    screen.getByText("Puedes buscar productos y registrar pedidos.");
    screen.getByText("Tus clientes son colombianos por WhatsApp.");

    // Los nombres de archivo son los reales del workspace (no "identity.md" inventado).
    screen.getByText("IDENTITY.md");
    screen.getByText("TOOLS.md");

    // El badge mockeado "v1.4.2" ya no existe.
    expect(screen.queryByText(/v1\.4\.2/)).toBeNull();
  });

  it("shows a placeholder (not a crash) when the backend returns no agents", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ agents: [] }));

    renderWithClient(<AgentsPrompts agentId="sales" />);

    await waitFor(() => screen.getByText("No hay agentes configurados."));
  });
});
