import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { AgentsMbaPreview } from "./AgentsMbaPreview";
import { MBA_CONFIG_FIXTURE } from "@plugins/agents_admin/frontend/entities/mba-config/fixture";

const fetchMock = vi.fn();

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

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
  cleanup();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("AgentsMbaPreview", () => {
  it("fetches the agent's mba-config and renders skills with their char budget", async () => {
    fetchMock.mockResolvedValue(jsonResponse(MBA_CONFIG_FIXTURE));

    renderWithClient(<AgentsMbaPreview agentId="sales" />);

    await waitFor(() => screen.getByText("persona-y-tono"));
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/api/agents/sales/mba-config");

    // presupuesto por skill: el guion supera los 20.000 y se marca, sin recortar
    screen.getByText("guion-sales-script");
    expect(screen.getByText(/20\.544/)).toBeTruthy();
    expect(screen.getAllByText(/excede/i).length).toBeGreaterThan(0);
    // trazabilidad: de qué archivos sale cada skill
    expect(screen.getAllByText("IDENTITY.md").length).toBeGreaterThan(0);
  });

  it("renders business info by Meta field name, marking fields without a source", async () => {
    fetchMock.mockResolvedValue(jsonResponse(MBA_CONFIG_FIXTURE));

    renderWithClient(<AgentsMbaPreview agentId="sales" />);

    await waitFor(() => screen.getByText("payment_method"));
    screen.getByText(/Nequi o llave 3229041190/);
    screen.getByText("delivery_and_shipping");
    // purchase_info viene vacío y email null: se ve que NO hay fuente, no un hueco
    expect(screen.getAllByText(/sin fuente/i).length).toBeGreaterThanOrEqual(2);
  });

  it("renders faqs, settings (handoff + never_say) and what is excluded with its reason", async () => {
    fetchMock.mockResolvedValue(jsonResponse(MBA_CONFIG_FIXTURE));

    renderWithClient(<AgentsMbaPreview agentId="sales" />);

    await waitFor(() => screen.getByText("¿Cuánto demora el envío?"));
    screen.getByText("Un colega del equipo te responde en este mismo chat 🤍");
    screen.getByText("voy a averiguar");
    screen.getByText("ALLOWLISTED_ONLY");
    screen.getByText("TOOLS.md#tool:set_order_slot");
    screen.getByText(/Tool interna de Hubara/);
    // cada bloque dice a qué endpoint de Meta va
    screen.getByText("/{entity_id}/agent_config/business_info");
  });

  it("renders the connector, its tools (method, path, macro, write flag) and the UI skills", async () => {
    fetchMock.mockResolvedValue(jsonResponse(MBA_CONFIG_FIXTURE));

    renderWithClient(<AgentsMbaPreview agentId="sales" />);

    await waitFor(() => screen.getByText("hubara-commerce"));
    screen.getByText("https://<host-publico>/api/mba");
    screen.getByText("X-API-Key");
    // connector tools con su request_definition
    screen.getByText("/tools/check_order_status");
    screen.getAllByText("WHATSAPP_PHONE_NUMBER");
    screen.getByText("/tools/register_order");
    expect(screen.getAllByText(/escritura/i).length).toBeGreaterThan(0);
    // UI skill nativa con su component_type
    screen.getByText("request-shipping-details");
    screen.getByText("flow");
    // y el mapa completo tool LLM → tratamiento
    screen.getByText("escalate_to_human");
    screen.getByText("native_handoff");
    screen.getByText("/{entity_id}/agent_connectors/{connector_id}/tools");
  });

  it("shows an error state instead of crashing when the backend fails", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "boom" }, 500));

    renderWithClient(<AgentsMbaPreview agentId="sales" />);

    await waitFor(() => screen.getByText(/No se pudo cargar/));
  });
});
