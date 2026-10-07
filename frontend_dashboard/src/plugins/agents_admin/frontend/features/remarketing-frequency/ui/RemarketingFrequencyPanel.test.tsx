import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { RemarketingFrequency } from "@plugins/agents_admin/frontend/entities/remarketing-frequency";
import fixture from "@plugins/agents_admin/frontend/entities/remarketing-frequency/fixtures/frequency.json";

import { RemarketingFrequencyPanel } from "./RemarketingFrequencyPanel";

/**
 * Agents → Remarketing → Frecuencia: cuántos toques máximo hace el bot por
 * cada vez que el cliente deja de contestar. A diferencia del bot nuevo (solo
 * por comando), ESTE ajuste se edita desde el dashboard (operador,
 * 2026-10-07): dentro del techo de Terraform, y aplica desde ya — por eso bajar
 * pide una confirmación inline (quien ya recibió más toques queda «Sin
 * respuesta»), y subir es directo.
 */

let state: RemarketingFrequency;
const puts: unknown[] = [];
let putFails: { status: number; body: unknown } | null = null;

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
  if (init?.method === "PUT") {
    const body = JSON.parse(String(init.body));
    puts.push(body);
    if (putFails) return json(putFails.body, putFails.status);
    state = {
      ...state,
      max_touches: body.max_touches,
      saved: body.max_touches,
      updated_at_ms: 1_790_200_000_000,
      updated_by: "dashboard:operator",
    };
    return json(state);
  }
  return json(state);
});

beforeEach(() => {
  state = structuredClone(fixture) as RemarketingFrequency;
  puts.length = 0;
  putFails = null;
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  fetchMock.mockClear();
});

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RemarketingFrequencyPanel />
    </QueryClientProvider>,
  );
}

const option = (n: number) => screen.getByRole("radio", { name: String(n) });

describe("RemarketingFrequencyPanel", () => {
  it("muestra la cantidad vigente entre 0 y el techo y cuándo sale cada toque", async () => {
    renderPanel();

    await waitFor(() => expect(option(5)).toHaveAttribute("aria-checked", "true"));
    expect(option(3)).toHaveAttribute("aria-checked", "false");
    // Escalera: +2 h, +4 h, +8 h, +14 h, +20 h desde el último mensaje del cliente.
    expect(screen.getByText("Toque 1")).toBeInTheDocument();
    expect(screen.getByText("2 h")).toBeInTheDocument();
    expect(screen.getByText("14 h")).toBeInTheDocument();
    expect(screen.getByText("20 h")).toBeInTheDocument();
  });

  it("no deja elegir por encima del techo de Terraform", async () => {
    state = { ...state, ceiling: 3, max_touches: 3 };
    renderPanel();

    await waitFor(() => expect(option(3)).toHaveAttribute("aria-checked", "true"));
    expect(option(3)).toBeEnabled();
    expect(option(4)).toBeDisabled();
    expect(option(5)).toBeDisabled();
    expect(screen.getByText(/techo de Terraform: 3/i)).toBeInTheDocument();
  });

  it("marca como «no se envía» los toques que quedan fuera de la cantidad elegida", async () => {
    state = { ...state, max_touches: 2, saved: 2 };
    renderPanel();

    await waitFor(() => expect(option(2)).toHaveAttribute("aria-checked", "true"));
    expect(screen.getAllByText(/no se envía/i)).toHaveLength(3);
  });

  it("subir la cantidad guarda directo, sin confirmación", async () => {
    state = { ...state, max_touches: 2, saved: 2 };
    renderPanel();
    await waitFor(() => expect(option(2)).toHaveAttribute("aria-checked", "true"));

    fireEvent.click(option(4));

    await waitFor(() => expect(puts).toEqual([{ max_touches: 4 }]));
    await waitFor(() => expect(option(4)).toHaveAttribute("aria-checked", "true"));
    expect(screen.queryByRole("button", { name: /confirmar/i })).toBeNull();
  });

  it("bajar la cantidad pide confirmar y NO guarda hasta confirmar", async () => {
    renderPanel();
    await waitFor(() => expect(option(5)).toHaveAttribute("aria-checked", "true"));

    fireEvent.click(option(2));

    expect(await screen.findByText(/Sin respuesta/)).toBeInTheDocument();
    expect(puts).toEqual([]);
    expect(option(5)).toHaveAttribute("aria-checked", "true");

    fireEvent.click(screen.getByRole("button", { name: /confirmar/i }));

    await waitFor(() => expect(puts).toEqual([{ max_touches: 2 }]));
    await waitFor(() => expect(option(2)).toHaveAttribute("aria-checked", "true"));
    expect(screen.queryByRole("button", { name: /confirmar/i })).toBeNull();
  });

  it("cancelar la baja no guarda nada", async () => {
    renderPanel();
    await waitFor(() => expect(option(5)).toHaveAttribute("aria-checked", "true"));

    fireEvent.click(option(1));
    fireEvent.click(await screen.findByRole("button", { name: /cancelar/i }));

    expect(puts).toEqual([]);
    expect(screen.queryByRole("button", { name: /confirmar/i })).toBeNull();
    expect(option(5)).toHaveAttribute("aria-checked", "true");
  });

  it("bajar a 0 avisa que el remarketing queda apagado", async () => {
    renderPanel();
    await waitFor(() => expect(option(5)).toHaveAttribute("aria-checked", "true"));

    fireEvent.click(option(0));

    expect(await screen.findByText(/no hará remarketing/i)).toBeInTheDocument();
  });

  it("muestra quién hizo el último cambio", async () => {
    state = { ...state, max_touches: 2, saved: 2, updated_at_ms: 1_790_200_000_000, updated_by: "dashboard:operator" };
    renderPanel();

    expect(await screen.findByText(/dashboard:operator/)).toBeInTheDocument();
  });

  it("si el servidor rechaza el cambio lo dice y deja la cantidad como estaba", async () => {
    state = { ...state, max_touches: 2, saved: 2 };
    putFails = { status: 422, body: { detail: { reason: "above_ceiling", ceiling: 3 } } };
    renderPanel();
    await waitFor(() => expect(option(2)).toHaveAttribute("aria-checked", "true"));

    fireEvent.click(option(4));

    expect(await screen.findByRole("alert")).toHaveTextContent(/no se pudo guardar/i);
    expect(option(2)).toHaveAttribute("aria-checked", "true");
  });

  it("si no puede leer el estado no inventa una cantidad", async () => {
    fetchMock.mockImplementationOnce(async () => json({ detail: "boom" }, 500));
    renderPanel();

    expect(await screen.findByText(/no se pudo leer/i)).toBeInTheDocument();
    expect(screen.queryByRole("radio")).toBeNull();
  });
});
