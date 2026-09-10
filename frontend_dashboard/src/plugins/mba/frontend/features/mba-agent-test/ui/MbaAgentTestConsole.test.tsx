import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { MbaAgentTestConsole } from "./MbaAgentTestConsole";

const fetchMock = vi.fn();

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}
function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}
beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

const reply = (over: Record<string, unknown> = {}) => ({
  ok: true,
  error: null,
  reply: { message_id: "m1", agent_response: "Hola, soy el asesor de Hubara.", conversation_id: "conv-1", timestamp: 1, ...over },
});

function posts(handler: (body: Record<string, unknown>, n: number) => Response) {
  const log: Record<string, unknown>[] = [];
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    const path = new URL(url, "http://x").pathname;
    if (path !== "/api/mba/agents/sales/test" || init?.method !== "POST") {
      return Promise.resolve(jsonResponse({ detail: `no route ${init?.method} ${path}` }, 500));
    }
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    log.push(body);
    return Promise.resolve(handler(body, log.length));
  });
  return log;
}

function send(text: string) {
  fireEvent.change(screen.getByPlaceholderText(/Escribí como el cliente/), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: /Enviar/ }));
}

describe("MbaAgentTestConsole", () => {
  it("sends a turn, shows both sides and threads the conversation_id on the next turn", async () => {
    const log = posts(() => jsonResponse(reply()));
    renderWithClient(<MbaAgentTestConsole agentId="sales" />);
    screen.getByText(/Nueva conversación/);
    send("hola");
    await waitFor(() => screen.getByText("Hola, soy el asesor de Hubara."));
    screen.getByText("hola");
    screen.getByText(/conv-1/);
    send("¿y envíos?");
    await waitFor(() => expect(log).toHaveLength(2));
    expect(log).toEqual([{ message: "hola" }, { message: "¿y envíos?", conversation_id: "conv-1" }]);
    expect(screen.getAllByText("Hola, soy el asesor de Hubara.")).toHaveLength(2);
  });

  it("a turn without text is not sent and a handoff / no-response reason is shown instead of an empty bubble", async () => {
    const log = posts(() =>
      jsonResponse(reply({ agent_response: "", no_response_reason: "handoff", handoff_reason: "customer_asked_for_human", quick_replies: ["Sí", "No"] })),
    );
    renderWithClient(<MbaAgentTestConsole agentId="sales" />);
    fireEvent.click(screen.getByRole("button", { name: /Enviar/ }));
    expect(log).toHaveLength(0);
    send("quiero un humano");
    await waitFor(() => screen.getByText(/sin respuesta: handoff/));
    screen.getByText(/customer_asked_for_human/);
    screen.getByText("Sí");
    screen.getByText("No");
  });

  it("new conversation drops the thread so the next turn starts without conversation_id", async () => {
    const log = posts(() => jsonResponse(reply()));
    renderWithClient(<MbaAgentTestConsole agentId="sales" />);
    send("hola");
    await waitFor(() => screen.getByText("Hola, soy el asesor de Hubara."));
    fireEvent.click(screen.getByRole("button", { name: /Nueva conversación/ }));
    expect(screen.queryByText("Hola, soy el asesor de Hubara.")).toBeNull();
    send("otra vez");
    await waitFor(() => expect(log).toHaveLength(2));
    expect(log[1]).toEqual({ message: "otra vez" });
  });

  it("a Meta rejection and a remote outage are visible with their real reason", async () => {
    posts((_, n) =>
      n === 1
        ? jsonResponse({ ok: false, reply: null, error: { kind: "rejected", status: 400, detail: "agent not onboarded" } })
        : jsonResponse({ detail: { error: "remote_unavailable", kind: "not_configured", status: null, detail: "META_MBA_TOKEN no configurado" } }, 503),
    );
    renderWithClient(<MbaAgentTestConsole agentId="sales" />);
    send("hola");
    await waitFor(() => screen.getByText(/Meta rechazó el turno: agent not onboarded/));
    send("hola de nuevo");
    await waitFor(() => screen.getByText(/META_MBA_TOKEN no configurado/));
    // el hilo de la consola sigue: los turnos del cliente quedan, el error no lo borra
    screen.getByText("hola");
    screen.getByText("hola de nuevo");
  });
});
