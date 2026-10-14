import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { DecisionsControlPanel } from "./DecisionsControlPanel";

/**
 * Motor de decisiones (diseño v2 §08 y §11, fase F7): cada capacidad con su
 * modo (reglas → sombra → canary → Jev) y la versión del workflow de ventas.
 * SOLO LECTURA desde el 2026-10-06: dice qué falta para subir y el comando;
 * los cambios van por comando, nunca desde el dashboard.
 */

const fetchMock = vi.fn();
let state: Record<string, unknown>;

function capability(mode: string, can: Record<string, string[]> = {}) {
  return {
    mode,
    ceiling: "on",
    facts: { days: 2, decisions: 40 },
    readiness: {
      shadow: [{ code: "within_ceiling", ok: true, detail: "techo de Terraform: on" }],
      canary: [{ code: "shadow_days", ok: false, detail: "2 días en sombra; mínimo 7" }],
      on: [{ code: "shadow_days", ok: false, detail: "2 días en sombra; mínimo 7" }],
    },
    can: { shadow: [], canary: ["shadow_days"], on: ["shadow_days"], ...can },
  };
}

function payload(caps: Record<string, string> = { baja: "off", compra: "shadow" }, workflow = "off") {
  return {
    state: { mode: "shadow", canary_percent: 10, test_numbers: [], updated_at_ms: null, updated_by: null },
    ceiling: "on",
    profile: "jev-v1",
    metrics: { days: 0, turns: 0, fallback_rate: null, p95_ms: null },
    readiness: {},
    can: {},
    capabilities: Object.fromEntries(Object.entries(caps).map(([name, mode]) => [name, capability(mode)])),
    workflow_v2: {
      mode: workflow,
      ceiling: "on",
      readiness: {
        canary: [{ code: "within_ceiling", ok: true, detail: "techo de Terraform: on" }],
        on: [{ code: "staged", ok: workflow !== "off", detail: "primero canary" }],
      },
      can: { canary: [], on: workflow === "off" ? ["staged"] : [] },
    },
  };
}

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

beforeEach(() => {
  state = payload();
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation(() => json(state));
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DecisionsControlPanel />
    </QueryClientProvider>,
  );
}

describe("DecisionsControlPanel", () => {
  it("lista cada capacidad con su modo y el workflow de ventas", async () => {
    renderPanel();

    const baja = await screen.findByRole("group", { name: "Baja" });
    expect(within(baja).getByText("Ahora: Reglas")).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Compra" })).getByText("Ahora: Sombra")).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Workflow de ventas" })).getByText("Ahora: V1")).toBeInTheDocument();
  });

  it("nombra las decisiones de la App Operador con su pregunta", async () => {
    state = payload({ burbuja: "off", incendio: "shadow" });
    renderPanel();

    const burbuja = await screen.findByRole("group", { name: "Burbuja principal (App Operador)" });
    expect(within(burbuja).getByText("¿qué acción del chat le conviene enviar al operador?")).toBeInTheDocument();
    const incendio = screen.getByRole("group", { name: "Incendio de chat (App Operador)" });
    expect(within(incendio).getByText("Ahora: Sombra")).toBeInTheDocument();
  });

  it("nombra los aromas de otro producto con su pregunta (ventas-7, 2026-10-13)", async () => {
    state = payload({ aroma_ajeno: "off" });
    renderPanel();

    const aroma = await screen.findByRole("group", { name: "Aromas de otro producto" });
    expect(within(aroma).getByText("¿el texto le ofrece al producto un aroma que es de otro?")).toBeInTheDocument();
    expect(within(aroma).getByText("Ahora: Reglas")).toBeInTheDocument();
  });

  it("nombra los colores de otro producto y la presentación que no existe", async () => {
    state = payload({ color_ajeno: "off", variante_ajena: "shadow" });
    renderPanel();

    const color = await screen.findByRole("group", { name: "Colores de otro producto" });
    expect(within(color).getByText("¿el texto le ofrece al producto un color que es de otro?")).toBeInTheDocument();
    const variant = screen.getByRole("group", { name: "Presentación que no existe" });
    expect(within(variant).getByText("¿el texto le ofrece una presentación, signo o tamaño que el producto no tiene? (solo con Jev)")).toBeInTheDocument();
  });

  it("nombra los hechos que el texto afirma (ventas-7)", async () => {
    state = payload({ precio_ajeno: "off", pago_ajeno: "off", estado_ajeno: "off", cupon_ajeno: "off", negacion_falsa: "off" });
    renderPanel();

    const price = await screen.findByRole("group", { name: "Precio que no es el del catálogo" });
    expect(within(price).getByText("¿el texto le dice un precio que no es el del catálogo?")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Pago que no aplica al pedido" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Estado del pedido que no es cierto" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Cupón que no está vigente" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Negar lo que sí tenemos" })).toBeInTheDocument();
  });

  it("nombra darle la razón al cliente en una cuenta equivocada (ventas-7)", async () => {
    state = payload({ cifra_del_cliente: "off" });
    renderPanel();

    const figure = await screen.findByRole("group", { name: "Darle la razón en una cuenta equivocada" });
    expect(within(figure).getByText("¿le da la razón al cliente en una cuenta que no es la de su pedido? (solo con Jev)")).toBeInTheDocument();
  });

  it("nombra el acuse tras la despedida con su pregunta", async () => {
    state = payload({ acuse: "off" });
    renderPanel();

    const acuse = await screen.findByRole("group", { name: "Acuse tras la despedida" });
    expect(within(acuse).getByText("¿el cliente solo agradece o se despide, sin pedir nada?")).toBeInTheDocument();
    expect(within(acuse).getByText("Ahora: Reglas")).toBeInTheDocument();
  });

  it("nombra la cortesía y el colega prometido con su pregunta (2026-09-30)", async () => {
    state = payload({ cortesia: "off", relevo: "off" });
    renderPanel();

    const cortesia = await screen.findByRole("group", { name: "Cortesía sin venta" });
    expect(within(cortesia).getByText("¿el cliente solo agradece o saluda, sin pedir nada?")).toBeInTheDocument();
    const relevo = screen.getByRole("group", { name: "Colega prometido" });
    expect(within(relevo).getByText("¿el mensaje promete que un colega lo atiende? (escala si nadie lo hizo)")).toBeInTheDocument();
  });

  it("nombra el preámbulo del modelo con su pregunta", async () => {
    state = payload({ preambulo: "shadow" });
    renderPanel();

    const preambulo = await screen.findByRole("group", { name: "Preámbulo del modelo" });
    expect(within(preambulo).getByText("¿la oración es una muletilla del modelo («Aquí tienes:»)?")).toBeInTheDocument();
    expect(within(preambulo).getByText("Ahora: Sombra")).toBeInTheDocument();
  });

  it("sin la vara dice qué falta para canary", async () => {
    renderPanel();

    const baja = await screen.findByRole("group", { name: "Baja" });
    expect(within(baja).getByText("Para canary falta:")).toBeInTheDocument();
    expect(within(baja).getByText("2 días en sombra; mínimo 7")).toBeInTheDocument();
  });

  it("no tiene nada que cambie el bot: da el comando y nunca escribe", async () => {
    renderPanel();

    await screen.findByRole("group", { name: "Baja" });
    // El único «botón» es el encabezado plegable del Panel compartido: no cambia nada del bot.
    const controls = screen.queryAllByRole("button").filter((b) => !b.textContent?.includes("Motor de decisiones"));
    expect(controls).toHaveLength(0);
    expect(screen.getByText(/Se cambia por comando/)).toBeInTheDocument();
    expect(screen.getByText(/src\.plugins\.chats\.agent\.sales\.decisions\.control/)).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });
});
