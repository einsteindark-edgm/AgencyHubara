import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { PerceptionRolloutPanel } from "./PerceptionRolloutPanel";

/**
 * Bot nuevo en la sección Agents: SOLO LECTURA desde el 2026-10-06 (decisión
 * del operador: «que los botones de la UI no sirvan y todo se haga por
 * comandos, para evitar que alguien jugando dañe producción»). Muestra qué
 * corre, lo medido en sombra, los números de prueba (tapados), si deciden con
 * Jev y qué falta para subir; para cambiar, el comando.
 */

const fetchMock = vi.fn();

function payload() {
  return {
    state: { mode: "shadow", canary_percent: 0, test_numbers: ["wa_573001234567"], updated_at_ms: 1, updated_by: "comando:ana" },
    ceiling: "canary",
    profile: "jev-v5",
    metrics: { days: 3, turns: 120, fallback_rate: 0.004, p95_ms: 820 },
    readiness: {
      shadow: [{ code: "signal_meta_on", ok: true, detail: "SALES_SIGNAL_INBOUND_META encendido" }],
      canary: [{ code: "shadow_days", ok: false, detail: "3 días en sombra; mínimo 7" }],
      on: [{ code: "within_ceiling", ok: false, detail: "techo de Terraform: canary" }],
    },
    can: { shadow: [], canary: ["shadow_days"], on: ["within_ceiling"] },
    test_numbers_jev: true,
  };
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation(() =>
    Promise.resolve(new Response(JSON.stringify(payload()), { status: 200, headers: { "content-type": "application/json" } })),
  );
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

describe("PerceptionRolloutPanel", () => {
  it("muestra el modo, el techo, el perfil, lo medido en sombra y quién hizo el último cambio", async () => {
    renderPanel();

    expect(await screen.findByText("Sombra")).toBeInTheDocument();
    expect(screen.getByText("canary")).toBeInTheDocument();
    expect(screen.getByText("jev-v5")).toBeInTheDocument();
    expect(screen.getByText(/3 días · 120 turnos/)).toBeInTheDocument();
    expect(screen.getByText(/comando:ana/)).toBeInTheDocument();
  });

  it("muestra los números de prueba tapados y si deciden con Jev", async () => {
    renderPanel();

    expect(await screen.findByText(/···4567/)).toBeInTheDocument();
    expect(screen.queryByText(/573001234567/)).not.toBeInTheDocument();
    expect(screen.getByText(/Deciden con Jev: sí/)).toBeInTheDocument();
  });

  it("dice qué falta para subir", async () => {
    renderPanel();

    expect(await screen.findByText("3 días en sombra; mínimo 7")).toBeInTheDocument();
  });

  it("no tiene nada que cambie el bot: da el comando y nunca escribe", async () => {
    renderPanel();

    await screen.findByText("Sombra");
    // El único «botón» es el encabezado plegable del Panel compartido: no cambia nada del bot.
    const controls = screen.queryAllByRole("button").filter((b) => !b.textContent?.includes("Bot nuevo"));
    expect(controls).toHaveLength(0);
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
    expect(screen.queryAllByRole("spinbutton")).toHaveLength(0);
    expect(screen.getByText(/Se cambia por comando/)).toBeInTheDocument();
    expect(screen.getByText(/src\.plugins\.chats\.agent\.sales\.decisions\.control/)).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });
});
