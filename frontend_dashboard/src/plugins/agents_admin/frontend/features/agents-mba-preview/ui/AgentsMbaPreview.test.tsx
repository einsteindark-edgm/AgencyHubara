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
    // el valor se ve en la vista legible Y dentro del body JSON del PUT
    expect(screen.getAllByText(/Nequi o llave 3229041190/).length).toBe(2);
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
    screen.getByText("TOOLS.md#tool:react_to_message");
    screen.getByText(/podría tomar el hilo/);
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
    // UI skill nativa con su component_type, y la aclaración estática/dinámica
    screen.getByText("request-shipping-details");
    screen.getByText("flow");
    screen.getByText("present-products");
    expect(screen.getAllByText(/estática/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/dinámica/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/a verificar en F0/i).length).toBeGreaterThan(0);
    // la aclaración de qué hace MBA y qué declaramos nosotros
    screen.getByText(/MBA renderiza/);
    // y el mapa completo tool LLM → endpoint de Meta (sin huecos: el que no viaja lo dice)
    screen.getByText("escalate_to_human");
    screen.getAllByText("connector_tool");
    screen.getByText("react_to_message");
    screen.getByText("unmapped");
    expect(screen.getAllByText("/{entity_id}/agent_connectors/{connector_id}/tools").length).toBeGreaterThan(1);
    expect(screen.getAllByText(/no viaja/i).length).toBeGreaterThan(1);
  });

  it("shows the workspace path and the numbered send sequence with full Meta URLs", async () => {
    fetchMock.mockResolvedValue(jsonResponse(MBA_CONFIG_FIXTURE));

    renderWithClient(<AgentsMbaPreview agentId="sales" />);

    await waitFor(() => screen.getByText("hubara_agency/src/plugins/chats/agent/sales/workspace"));
    // secuencia: 12 requests numerados, con la URL completa (no solo el path)
    screen.getByText(/12 requests/);
    expect(
      screen.getAllByText("https://api.facebook.com/{entity_id}/agent_config/skills").length,
    ).toBeGreaterThanOrEqual(1);
    expect(
      screen.getAllByText("https://api.facebook.com/{entity_id}/agent_config/allowlist").length,
    ).toBeGreaterThanOrEqual(1);
    screen.getByText(/\+57XXXXXXXXXX/);
  });

  it("renders, per skill, the exact JSON body that would be POSTed (full text, no summary)", async () => {
    fetchMock.mockResolvedValue(jsonResponse(MBA_CONFIG_FIXTURE));

    renderWithClient(<AgentsMbaPreview agentId="sales" />);

    await waitFor(() => screen.getByText("persona-y-tono"));
    // el JSON literal del request, con el texto completo de la skill adentro
    expect(screen.getAllByText(/"skill": "# Eres el Asesor/).length).toBe(1);
    expect(screen.getAllByText(/"title": "reglas-operativas"/).length).toBe(1);
    // los headers exactos que viajan con cada request
    expect(screen.getAllByText(/X-API-Version: 2\.0\.0/).length).toBeGreaterThan(1);
    // el body del connector tool con la macro del teléfono
    expect(screen.getAllByText(/"macro": "WHATSAPP_PHONE_NUMBER"/).length).toBe(2);
    // never_say_phrases viaja como lista plana de strings
    screen.getByText(/"never_say_phrases": \[/);
  });

  it("shows an error state instead of crashing when the backend fails", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "boom" }, 500));

    renderWithClient(<AgentsMbaPreview agentId="sales" />);

    await waitFor(() => screen.getByText(/No se pudo cargar/));
  });
});
