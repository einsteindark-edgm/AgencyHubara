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

  it("si no se puede leer el estado, Apagar sigue ahí", async () => {
    fetchMock.mockImplementation((_url: string, init?: RequestInit) =>
      init?.method === "PUT" ? json(payload("off")) : json({ detail: "timeout" }, 504),
    );
    renderPanel();

    expect(await screen.findByText(/No se pudo leer el estado del encendido/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Apagar" }));

    await waitFor(() => expect(puts().at(-1)?.[1]).toEqual({ mode: "off" }));
  });

  it("Apagar no espera a un cambio que sigue en curso", async () => {
    state = payload("shadow", { canary: [] });
    fetchMock.mockImplementation((_url: string, init?: RequestInit) => {
      if (init?.method !== "PUT") return json(state);
      const body = JSON.parse(String(init.body));
      return body.mode === "off" ? json(payload("off")) : new Promise<Response>(() => {});
    });
    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "Canary" }));
    const confirm = screen.getByRole("button", { name: "Sí, pasar a canary" });
    fireEvent.click(confirm);
    await waitFor(() => expect(confirm).toBeDisabled()); // el cambio a canary sigue en vuelo
    const off = screen.getByRole("button", { name: "Apagar" });
    expect(off).toBeEnabled();
    fireEvent.click(off);

    await waitFor(() => expect(puts().map(([, b]) => b.mode)).toEqual(["canary", "off"]));
  });

  it("un 504 dice que el cambio puede haberse aplicado y vuelve a leer el estado", async () => {
    state = payload("shadow", { canary: [] });
    fetchMock.mockImplementation((_url: string, init?: RequestInit) =>
      init?.method === "PUT" ? json({ detail: "El proveedor tardó: PUEDE haberse aplicado" }, 504) : json(state),
    );
    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "Canary" }));
    const gets = () => fetchMock.mock.calls.filter(([, init]) => init?.method !== "PUT").length;
    const before = gets();
    fireEvent.click(screen.getByRole("button", { name: "Sí, pasar a canary" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("puede haberse aplicado");
    await waitFor(() => expect(gets()).toBeGreaterThan(before));
  });

  it("la confirmación de canary dice a cuántas conversaciones llega", async () => {
    state = payload("shadow", { canary: [] });
    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "Canary" }));

    expect(screen.getByRole("group", { name: "Confirmar el cambio de modo" })).toHaveTextContent(
      "al 10 % de las conversaciones y a 1 número de prueba",
    );
  });

  it("estando en canary, cambiar el porcentaje se aplica con confirmación", async () => {
    state = payload("canary", { canary: [], on: ["shadow_days"] });
    renderPanel();

    fireEvent.change(await screen.findByLabelText("Porcentaje canary"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: "Aplicar" }));
    expect(puts()).toEqual([]);
    const confirm = screen.getByRole("group", { name: "Confirmar el cambio de modo" });
    expect(confirm).toHaveTextContent("al 20 % de las conversaciones");
    fireEvent.click(within(confirm).getByRole("button", { name: "Sí, aplicar" }));

    await waitFor(() => expect(puts().at(-1)?.[1]).toEqual({ mode: "canary", canary_percent: 20, test_numbers: ["wa_573001234567"] }));
  });

  it("dice quién hizo el último cambio y el modo que de verdad corre bajo el techo", async () => {
    state = {
      ...payload("on", { on: [] }),
      ceiling: "shadow",
      state: { mode: "on", canary_percent: 0, test_numbers: [], updated_at_ms: 1_790_200_000_000, updated_by: "operadora" },
    };
    renderPanel();

    expect(await screen.findByText("Encendido (corre en sombra por el techo)")).toBeInTheDocument();
    expect(screen.getByText(/por operadora/)).toBeInTheDocument();
  });
});
