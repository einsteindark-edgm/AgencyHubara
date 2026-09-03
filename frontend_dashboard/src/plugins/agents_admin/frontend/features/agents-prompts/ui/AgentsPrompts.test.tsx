import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { AgentsPrompts } from "./AgentsPrompts";
import { MBA_CONFIG_FIXTURE } from "@plugins/agents_admin/frontend/entities/mba-config/fixture";

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

const REMARKETING_AGENT = {
  ...SALES_AGENT,
  id: "remarketing",
  name: "Remarketing",
  role: "Reactivación por WhatsApp",
  category: "Remarketing",
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

  it("ofrece los tabs Personalidad | Calidad LLM solo para el agente sales", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ agents: [SALES_AGENT] }));

    renderWithClient(<AgentsPrompts agentId="sales" />);

    await waitFor(() => screen.getByText("Asesor de Ventas"));

    // El canvas de `sales` ofrece ambos tabs; "Personalidad" es el default
    // (los prompts siguen visibles, el panel de Calidad LLM no se monta aún).
    expect(screen.getByRole("button", { name: /Personalidad/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Calidad LLM/i })).toBeTruthy();
    screen.getByText("Eres el Asesor Exclusivo de Ventas de Hubara.");
  });

  it("no ofrece el tab Calidad LLM para agentes sin harness de evals", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ agents: [REMARKETING_AGENT] }));

    renderWithClient(<AgentsPrompts agentId="remarketing" />);

    await waitFor(() => screen.getByText("Remarketing"));

    // Solo `sales` tiene panel de Calidad LLM; pero TODOS los agentes tienen
    // Personalidad + Meta Business Agent (qué le mandaríamos a MBA de cada bot).
    expect(screen.queryByRole("button", { name: /Calidad LLM/i })).toBeNull();
    expect(screen.getByRole("button", { name: /Personalidad/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Meta Business Agent/i })).toBeTruthy();
  });

  it("al abrir el tab Meta Business Agent monta el preview normalizado del agente", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/mba-config")) return Promise.resolve(jsonResponse(MBA_CONFIG_FIXTURE));
      return Promise.resolve(jsonResponse({ agents: [SALES_AGENT] }));
    });

    renderWithClient(<AgentsPrompts agentId="sales" />);
    await waitFor(() => screen.getByText("Asesor de Ventas"));

    fireEvent.click(screen.getByRole("button", { name: /Meta Business Agent/i }));

    // El preview pide /api/agents/sales/mba-config y muestra las skills normalizadas.
    await waitFor(() => screen.getByText("persona-y-tono"));
    expect(
      fetchMock.mock.calls.some((c) => String(c[0]).includes("/api/agents/sales/mba-config")),
    ).toBe(true);
    // Los prompts crudos ya no se muestran en este tab.
    expect(screen.queryByText("Eres el Asesor Exclusivo de Ventas de Hubara.")).toBeNull();
  });
});
