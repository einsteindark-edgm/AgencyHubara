import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { DecisionEngineView } from "./DecisionEngineView";

/**
 * La pestaña «Motor de decisiones» muestra TODOS los paquetes que corren en el
 * motor oficial: el de la tienda y el de la App Operador (2026-10-06), con cada
 * decisión donde actúa y el paquete del que sale.
 */

const ENGINE = {
  bundle: { id: "ventas", version: 1, ref: "ventas@1", oracle: "jev-1.13", engine_contract: 1, code_default: "ventas" },
  bundles: [
    { id: "ventas", version: 1, ref: "ventas@1", oracle: "jev-1.13", name: "La tienda: el bot de ventas y remarketing" },
    { id: "operador", version: 1, ref: "operador@1", oracle: "jev-1.13", name: "App Operador: el chat y los incendios del teléfono" },
  ],
  profile: "jev-v5",
  places: [
    { id: "ingest", label: "Al llegar un mensaje del cliente" },
    { id: "chat_operador", label: "En el chat de la App Operador, cuando atiende una persona" },
  ],
  decisions: [
    { capability: "baja", name: "Pide la baja", where: ["ingest"], solves: "Lee si pide no recibir más.", mode: "off", bundle: "ventas@1" },
    {
      capability: "burbuja",
      name: "Qué acción va primero en el chat",
      where: ["chat_operador"],
      solves: "Elige la acción que le conviene enviar al operador.",
      mode: "shadow",
      bundle: "operador@1",
    },
  ],
  turn: null,
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(JSON.stringify(ENGINE), { status: 200, headers: { "content-type": "application/json" } }))),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderView() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DecisionEngineView />
    </QueryClientProvider>,
  );
}

describe("DecisionEngineView", () => {
  it("nombra los paquetes que corren en el motor", async () => {
    renderView();

    const version = await screen.findByRole("region", { name: "Versión del motor de decisiones" });
    expect(within(version).getByText(/operador@1/)).toBeInTheDocument();
    expect(within(version).getByText(/App Operador: el chat y los incendios del teléfono/)).toBeInTheDocument();
  });

  it("pone las decisiones de la App Operador donde actúan, con su paquete", async () => {
    renderView();

    const chat = await screen.findByRole("region", { name: "En el chat de la App Operador, cuando atiende una persona" });
    const burbuja = within(chat).getByRole("listitem", { name: "Qué acción va primero en el chat" });
    expect(within(burbuja).getByText("paquete operador@1")).toBeInTheDocument();
    expect(within(burbuja).getByText("Jev en sombra: mide, decide la regla")).toBeInTheDocument();
    // Las de la tienda no repiten su paquete: es el de arriba.
    const baja = screen.getByRole("listitem", { name: "Pide la baja" });
    expect(within(baja).queryByText(/paquete/)).toBeNull();
  });
});
