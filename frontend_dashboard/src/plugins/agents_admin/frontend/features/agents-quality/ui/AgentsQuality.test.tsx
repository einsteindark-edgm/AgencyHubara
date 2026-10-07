import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import statsFixture from "@plugins/agents_admin/frontend/entities/check-stats/fixtures/check-stats.json";
import calibrationFixture from "@plugins/agents_admin/frontend/entities/eval-label/fixtures/calibration.json";
import labelsFixture from "@plugins/agents_admin/frontend/entities/eval-label/fixtures/labels.json";
import queueFixture from "@plugins/agents_admin/frontend/entities/eval-label/fixtures/labels-queue.json";
import conversationsFixture from "@plugins/agents_admin/frontend/entities/production-quality/fixtures/conversations.json";
import engineFixture from "@plugins/agents_admin/frontend/entities/perception-rollout/fixtures/engine.json";
import evaluationsFixture from "@plugins/agents_admin/frontend/entities/production-quality/fixtures/evaluations.json";
import jevFixture from "@plugins/agents_admin/frontend/entities/production-quality/fixtures/jev.json";
import threadFixture from "@plugins/agents_admin/frontend/entities/production-quality/fixtures/thread.json";
import traceFixture from "@plugins/agents_admin/frontend/entities/production-quality/fixtures/turn-trace.json";
import checksFixture from "@plugins/agents_admin/frontend/entities/scorecard/fixtures/checks.json";
import listFixture from "@plugins/agents_admin/frontend/entities/scorecard/fixtures/scorecards.json";

import { AgentsQuality } from "./AgentsQuality";

/**
 * Calidad LLM con la vista del laboratorio sobre producción (decisión del
 * operador, 2026-10-02): el Resumen trae las gráficas, la matriz y el informe
 * de Jev; Conversaciones, cada conversación real como un hilo con cada turno
 * calificado y la ventana del turno (resultado, paso a paso, decisiones de
 * Jev). El filtro separa el bot actual del bot Jev (el workflow nuevo).
 */

const fetchMock = vi.fn();
let statsPayload: unknown = statsFixture;

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

const S1 = "wa_100000000001";

/** Router de fetch por endpoint (orden: rutas más específicas primero). */
const ROUTES: Array<[string, () => unknown]> = [
  ["/api/agents/perception/engine", () => engineFixture],
  [`/api/agents/evals/production/conversations/${S1}/turns/trace`, () => traceFixture],
  [`/api/agents/evals/production/conversations/${S1}/evaluations`, () => evaluationsFixture],
  [`/api/agents/evals/production/conversations/${S1}`, () => threadFixture],
  ["/api/agents/evals/production/conversations", () => conversationsFixture],
  ["/api/agents/evals/production/jev", () => jevFixture],
  ["/api/agents/evals/checks/stats", () => statsPayload],
  ["/api/agents/evals/checks", () => checksFixture],
  ["/api/agents/evals/scorecards", () => listFixture],
  ["/api/agents/evals/labels/queue", () => queueFixture],
  ["/api/agents/evals/labels?", () => labelsFixture],
  ["/api/agents/evals/calibration", () => calibrationFixture],
];

function called(fragment: string): boolean {
  return fetchMock.mock.calls.some(([u]) => String(u).includes(fragment));
}

beforeEach(() => {
  statsPayload = statsFixture;
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((url: string) => {
    const hit = ROUTES.find(([p]) => url.includes(p));
    return Promise.resolve(json(hit ? hit[1]() : {}));
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function renderIt() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AgentsQuality />
    </QueryClientProvider>,
  );
}

async function openConversations() {
  fireEvent.click(screen.getByRole("tab", { name: /conversaciones/i }));
  return screen.findByRole("list", { name: /conversaciones calificadas/i });
}

describe("Calidad LLM: Resumen como el laboratorio", () => {
  it("abre en Resumen con las gráficas y la matriz de cada episodio", async () => {
    renderIt();
    expect(screen.getByRole("tab", { name: /resumen/i })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("heading", { name: /cumplimiento por check, semana a semana/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /dónde terminan los episodios/i })).toBeInTheDocument();
    expect(await screen.findByRole("table", { name: /matriz de cumplimiento/i })).toBeInTheDocument();
    // La vista de antes (tiles de veredictos y Pareto) ya no está.
    expect(screen.queryByRole("list", { name: /veredictos de los episodios/i })).not.toBeInTheDocument();
  });

  it("dice cómo anduvo Jev en producción", async () => {
    renderIt();
    const details = await screen.findByText(/jev en producción/i);
    fireEvent.click(details);
    const jev = await screen.findByRole("region", { name: /jev en producción/i });
    expect(within(jev).getByText(/50 % de las preguntas/i)).toBeInTheDocument();
    expect(within(jev).getByText(/1 de 2 decisiones cayeron a la regla porque jev falló/i)).toBeInTheDocument();
    await waitFor(() => expect(called("/api/agents/evals/production/jev?days=56&bot=nuevo")).toBe(true));
  });

  it("empieza por la matriz y sigue con cada bot, dónde terminan y la semana a semana (operador, 2026-10-07)", async () => {
    renderIt();
    const order = [
      await screen.findByRole("heading", { name: /cada episodio, check por check/i }),
      await screen.findByRole("heading", { name: /cómo le fue a cada bot/i }),
      await screen.findByRole("heading", { name: /dónde terminan los episodios/i }),
      await screen.findByRole("heading", { name: /cumplimiento por check, semana a semana/i }),
    ];
    for (let i = 1; i < order.length; i++) {
      expect(order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it("cómo le fue a cada bot va en porcentajes, sin abrir nada", async () => {
    renderIt();
    const table = await screen.findByRole("table", { name: /resultado por bot/i });
    // 18 de 42 episodios pasan; 13 en alerta; 9 fallan (el mismo fixture para los dos bots).
    await waitFor(() => expect(within(table).getAllByText("42,9 %")).toHaveLength(2));
    expect(within(table).getAllByText("31 %")).toHaveLength(2);
    expect(within(table).getAllByText("21,4 %")).toHaveLength(2);
  });

  it("dice el cumplimiento de cada etapa por bot", async () => {
    renderIt();
    const table = await screen.findByRole("table", { name: /cumplimiento por etapa/i });
    // Descubrimiento: 85 de 102 veces que aplicó, pasó.
    const row = await within(table).findByRole("row", { name: /descubrimiento/i });
    expect(within(row).getAllByText("83,3 %")).toHaveLength(2);
    expect(within(table).getByRole("row", { name: /variantes/i })).toHaveTextContent("86,3 %");
  });

  it("el código de un check explica qué califica", async () => {
    renderIt();
    const table = await screen.findByRole("table", { name: /matriz de cumplimiento/i });
    fireEvent.click(await within(table).findByRole("button", { name: /^APE-01/ }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: /APE-01 · Saludo por hora y marca en el primer contacto/i })).toBeInTheDocument();
    expect(within(dialog).getByText("Primer contacto de la conversación.")).toBeInTheDocument();
    expect(within(dialog).getByText(/abre con saludo por hora de Colombia/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/si falla, la conversación queda en alerta/i)).toBeInTheDocument();
    // Explicar no abre la conversación.
    expect(screen.getByRole("tab", { name: /resumen/i })).toHaveAttribute("aria-selected", "true");
  });

  it("una falla de la matriz lleva al turno que la tiene", async () => {
    renderIt();
    const table = await screen.findByRole("table", { name: /matriz de cumplimiento/i });
    const firstRow = within(table).getAllByRole("row").filter((r) => r.closest("tbody"))[0];
    fireEvent.click(within(firstRow).getByRole("button", { name: /DES-05 · falla/i }));

    expect(screen.getByRole("tab", { name: /conversaciones/i })).toHaveAttribute("aria-selected", "true");
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Hilo del turno 3")).toBeInTheDocument();
  });

  it("una fila de la matriz abre esa conversación", async () => {
    renderIt();
    const table = await screen.findByRole("table", { name: /matriz de cumplimiento/i });
    fireEvent.click(within(table).getAllByRole("row").filter((r) => r.closest("tbody"))[0]);

    expect(screen.getByRole("tab", { name: /conversaciones/i })).toHaveAttribute("aria-selected", "true");
    const list = await screen.findByRole("list", { name: /conversaciones calificadas/i });
    expect(within(list).getByRole("button", { name: /cliente ···0001/i })).toHaveAttribute("aria-current", "true");
  });

  it("explica el vacío cuando todavía no hay episodios calificados", async () => {
    statsPayload = { episodes: 0 };
    renderIt();
    expect(await screen.findByText(/aún no hay episodios calificados/i)).toBeInTheDocument();
  });
});

describe("Calidad LLM: Conversaciones como el laboratorio", () => {
  it("lista las conversaciones reales con su resultado y su bot", async () => {
    renderIt();
    const list = await openConversations();
    const first = within(list).getByRole("button", { name: /cliente ···0001/i });
    expect(within(first).getByText("FALLA")).toBeInTheDocument();
    expect(within(first).getByText("Bot Jev")).toBeInTheDocument();
    expect(within(list).getByRole("button", { name: /cliente ···0005/i })).toBeInTheDocument();
  });

  it("muestra el hilo real con cada turno calificado", async () => {
    renderIt();
    await openConversations();
    expect(await screen.findByText("velas de lavanda")).toBeInTheDocument();
    const chips = await screen.findAllByRole("button", { name: /ver hilo del turno/i });
    expect(chips).toHaveLength(2);
    await waitFor(() => expect(within(chips[1]).getByText("FALLA")).toBeInTheDocument());
    expect(within(chips[0]).getByText("PASA")).toBeInTheDocument();
  });

  it("la ventana del turno trae el resultado, el paso a paso y las decisiones de Jev", async () => {
    renderIt();
    await openConversations();
    const chips = await screen.findAllByRole("button", { name: /ver hilo del turno/i });
    fireEvent.click(chips[1]);

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Hilo del turno 3")).toBeInTheDocument();
    expect(await within(dialog).findByText("Busca en el catálogo antes de nombrar productos", { exact: false })).toBeInTheDocument();
    expect(within(dialog).getByText("Tenemos la vela de lavanda a $30.000")).toBeInTheDocument();

    fireEvent.click(await within(dialog).findByRole("tab", { name: /decisiones de jev/i }));
    expect(within(dialog).getByText(/jev decidió 1 de 2/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/jev no respondió a tiempo/i)).toBeInTheDocument();
    await waitFor(() => expect(called(`/production/conversations/${S1}/turns/trace?turn_key=`)).toBe(true));
  });

  it("«Qué falló» dice cada check por su nombre y abre su turno", async () => {
    renderIt();
    await openConversations();
    fireEvent.click(await screen.findByRole("tab", { name: /qué falló/i }));
    const failed = await screen.findByRole("list", { name: /lo que falló/i });
    expect(within(failed).getByText(/busca en el catálogo antes de nombrar/i)).toBeInTheDocument();
    fireEvent.click(within(failed).getByRole("button", { name: "turno 3" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("la alerta lleva a las conversaciones que fallaron", async () => {
    renderIt();
    fireEvent.click(await screen.findByRole("button", { name: /2 episodios para revisar/i }));
    const list = await screen.findByRole("list", { name: /conversaciones calificadas/i });
    expect(within(list).getAllByRole("button")).toHaveLength(1);
    expect(within(list).getByRole("button", { name: /cliente ···0001/i })).toBeInTheDocument();
  });
});

describe("Calidad LLM: el filtro por bot", () => {
  it("«Bot Jev» pide solo las conversaciones del workflow nuevo", async () => {
    renderIt();
    await screen.findByRole("table", { name: /matriz de cumplimiento/i });

    const picker = screen.getByRole("radiogroup", { name: "Bot que respondió" });
    expect(within(picker).getByRole("radio", { name: "Todos" })).toBeChecked();
    fireEvent.click(within(picker).getByRole("radio", { name: "Bot Jev" }));

    await waitFor(() => expect(called("/checks/stats?days=56&bot=nuevo")).toBe(true));
    await waitFor(() => expect(called("/scorecards?days=56&bot=nuevo")).toBe(true));
    await openConversations();
    await waitFor(() => expect(called("/production/conversations?days=56&bot=nuevo")).toBe(true));
  });

  it("el filtro solo aparece en las vistas que filtra", async () => {
    renderIt();
    expect(screen.getByRole("radiogroup", { name: "Bot que respondió" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /métricas legadas/i }));
    expect(screen.queryByRole("radiogroup", { name: "Bot que respondió" })).not.toBeInTheDocument();
  });

  it("si el servidor no filtró por bot, lo dice (API sin desplegar)", async () => {
    renderIt();
    await screen.findByRole("table", { name: /matriz de cumplimiento/i });
    fireEvent.click(screen.getByRole("radio", { name: "Bot Jev" }));
    expect(await screen.findByText(/el servidor no filtró por bot/i)).toBeInTheDocument();
  });

  it("con filtro, el vacío dice de qué bot", async () => {
    statsPayload = { episodes: 0 };
    renderIt();
    await screen.findByText(/aún no hay episodios calificados/i);
    fireEvent.click(screen.getByRole("radio", { name: "Bot Jev" }));
    expect(await screen.findByText(/aún no hay episodios del bot jev/i)).toBeInTheDocument();
  });
});

describe("Calidad LLM: el motor de decisiones", () => {
  async function openEngine() {
    fireEvent.click(screen.getByRole("tab", { name: /motor de decisiones/i }));
    return screen.findByRole("region", { name: /versión del motor de decisiones/i });
  }

  it("dice qué versión del motor corre la tienda", async () => {
    renderIt();
    const version = await openEngine();
    expect(within(version).getByText(/ventas-2/)).toBeInTheDocument();
    expect(within(version).getByText(/versión 2/i)).toBeInTheDocument();
    expect(within(version).getByText(/jev 1\.13/i)).toBeInTheDocument();
    // El del código es otro: lo eligió la configuración de la tienda.
    expect(within(version).getByText(/el que trae el código es «ventas»/i)).toBeInTheDocument();
    await waitFor(() => expect(called("/api/agents/perception/engine")).toBe(true));
  });

  it("lista cada decisión por la parte del software donde actúa, con lo que resuelve", async () => {
    renderIt();
    await openEngine();
    const ingest = screen.getByRole("region", { name: "Al leer cada mensaje del cliente" });
    const compra = within(ingest).getByRole("listitem", { name: "Compra confirmada" });
    expect(within(compra).getByText(/un «sí» que respondía otra cosa/i)).toBeInTheDocument();
    expect(within(compra).getByText("Jev decide")).toBeInTheDocument();
    expect(within(within(ingest).getByRole("listitem", { name: "Baja de mensajes" })).getByText(/jev en sombra/i)).toBeInTheDocument();
    const tools = screen.getByRole("region", { name: "Dentro de las herramientas del bot" });
    expect(within(tools).getByText(/variante de «para quién es el texto»/i)).toBeInTheDocument();
    // El turno del bot Jev también es parte del motor.
    expect(screen.getByText(/12 asuntos/i)).toBeInTheDocument();
  });
});

describe("Calidad LLM: las otras pestañas", () => {
  it("conserva calibración, métricas legadas y goldens", async () => {
    renderIt();
    fireEvent.click(screen.getByRole("tab", { name: /métricas legadas/i }));
    expect(await screen.findByText("Tendencia de calidad")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /goldens/i }));
    expect(await screen.findByText("Candidatos a golden")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /calibración/i }));
    expect(await screen.findByRole("table", { name: /calibración del juez/i })).toBeInTheDocument();
  });
});
