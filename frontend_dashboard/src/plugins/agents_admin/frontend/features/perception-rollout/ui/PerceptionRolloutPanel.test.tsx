import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { PerceptionRolloutPanel } from "./PerceptionRolloutPanel";

/**
 * Encendido del bot nuevo en la sección Agents (plan del laboratorio PR 16).
 * Apagar es inmediato y siempre está; subir de modo se apaga si un chequeo
 * falla (y dice cuál); canary y encendido piden confirmar en dos pasos.
 */

const fetchMock = vi.fn();
let state: Record<string, unknown>;

function payload(mode: string, can: Record<string, string[]> = {}) {
  return {
    state: { mode, canary_percent: 10, test_numbers: ["wa_573001234567"], updated_at_ms: 1, updated_by: "dashboard" },
    ceiling: "on",
    profile: "jev-v1",
    metrics: { days: 3, turns: 120, fallback_rate: 0.004, p95_ms: 820 },
    readiness: {
      shadow: [{ code: "signal_meta_on", ok: true, detail: "SALES_SIGNAL_INBOUND_META encendido" }],
      canary: [{ code: "shadow_days", ok: false, detail: "3 días en sombra (120 turnos); mínimo 7" }],
      on: [{ code: "shadow_days", ok: false, detail: "3 días en sombra (120 turnos); mínimo 7" }],
    },
    can: { shadow: [], canary: ["shadow_days"], on: ["shadow_days"], ...can },
  };
}

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

beforeEach(() => {
  state = payload("shadow");
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((_url: string, init?: RequestInit) => {
    if (init?.method === "PUT") {
      const body = JSON.parse(String(init.body));
      state = payload(body.mode, { canary: [], on: [] });
      return json(state);
    }
    return json(state);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PerceptionRolloutPanel />
    </QueryClientProvider>,
  );
}

function puts() {
  return fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT").map(([u, init]) => [String(u), JSON.parse(init.body)]);
}

describe("PerceptionRolloutPanel", () => {
  it("muestra el modo, el techo, el perfil y la sombra medida", async () => {
    renderPanel();

    expect(await screen.findByText("Sombra")).toBeInTheDocument();
    expect(screen.getByText("on")).toBeInTheDocument();
    expect(screen.getByText("jev-v1")).toBeInTheDocument();
    expect(screen.getByText("3 días · 120 turnos · caídas 0,4 % · p95 820 ms")).toBeInTheDocument();
  });

  it("apagar es inmediato", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "Apagar" }));

    await waitFor(() => expect(puts()).toEqual([[expect.stringContaining("/api/agents/perception/rollout"), { mode: "off" }]]));
  });

  it("un modo que no cumple la vara queda apagado y dice por qué", async () => {
    renderPanel();

    const on = await screen.findByRole("button", { name: "Encender" });
    expect(on).toBeDisabled();
    expect(screen.getAllByText("3 días en sombra (120 turnos); mínimo 7").length).toBeGreaterThan(0);
  });

  it("canary pide confirmar antes de enviar", async () => {
    state = payload("shadow", { canary: [] });
    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "Canary" }));
    expect(puts()).toEqual([]);
    const confirm = screen.getByRole("group", { name: "Confirmar el cambio de modo" });
    fireEvent.click(within(confirm).getByRole("button", { name: "Sí, pasar a canary" }));

    await waitFor(() => expect(puts().at(-1)?.[1]).toEqual({ mode: "canary", canary_percent: 10, test_numbers: ["wa_573001234567"] }));
  });

  it("un 422 del servidor muestra los chequeos que fallan", async () => {
    state = payload("shadow", { canary: [] });
    fetchMock.mockImplementation((_url: string, init?: RequestInit) =>
      init?.method === "PUT"
        ? json({ detail: { reason: "not_ready", failing: ["shadow_p95"], readiness: [{ code: "shadow_p95", ok: false, detail: "p95 de la percepción: 2100 ms" }] } }, 422)
        : json(state),
    );
    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "Canary" }));
    fireEvent.click(screen.getByRole("button", { name: "Sí, pasar a canary" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("p95 de la percepción: 2100 ms");
  });
});
