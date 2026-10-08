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
 * operador, 2026-10-02), reorganizada el 2026-10-08: el Resumen compara a los
 * dos bots — Botsito (el workflow actual) y Colossus (el bot Jev) —; cada bot
 * tiene su propia sección con sus gráficas y su ficha técnica; Conversaciones
 * trae su propio filtro por bot. La pestaña «Métricas legadas» ya no existe:
 * su «Tendencia de calidad» vive en el Resumen y en cada bot.
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

const trendFixture = {
  threshold: 0.7,
  suite: "online",
  metrics: ["tono"],
  series: [{ metric: "tono", points: [{ date: "2026-10-01", avg: 0.82, min: 0.6, n: 3, n_below: 1 }] }],
};

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
  ["/api/agents/evals/history", () => trendFixture],
  ["/api/agents/evals/conversations", () => ({ conversations: [] })],
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

async function openBot(name: "Botsito" | "Colossus") {
  fireEvent.click(screen.getByRole("tab", { name }));
  return screen.findByRole("complementary", { name: `Especificaciones técnicas de ${name}` });
}

function before(a: Element, b: Element): boolean {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

describe("Calidad LLM: las pestañas (operador, 2026-10-08)", () => {
  it("cada bot es una sección propia y las métricas legadas ya no están", () => {
    renderIt();
    const names = screen.getAllByRole("tab").map((t) => t.textContent?.trim());
    expect(names).toEqual(["Resumen", "Botsito", "Colossus", "Conversaciones", "Motor de decisiones", "Calibración", "Goldens"]);
    // El filtro por bot ya no vive en la barra de pestañas.
    expect(screen.queryByRole("radiogroup", { name: "Bot que respondió" })).not.toBeInTheDocument();
  });
});

describe("Calidad LLM: el Resumen compara a los dos bots", () => {
  it("sigue el orden: cada bot, dónde terminan, la semana a semana y la tendencia de calidad; sin la matriz", async () => {
    renderIt();
    expect(screen.getByRole("tab", { name: /resumen/i })).toHaveAttribute("aria-selected", "true");
    const order = [
      await screen.findByRole("heading", { name: /cómo le fue a cada bot/i }),
      await screen.findByRole("heading", { name: /dónde terminan los episodios/i }),
      await screen.findByRole("heading", { name: /cumplimiento por check, semana a semana/i }),
      await screen.findByRole("heading", { name: /tendencia de calidad/i }),
    ];
    for (let i = 1; i < order.length; i++) expect(before(order[i - 1], order[i])).toBe(true);
    expect(screen.queryByRole("heading", { name: /cada episodio, check por check/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("table", { name: /matriz de cumplimiento/i })).not.toBeInTheDocument();
  });

  it("cómo le fue a cada bot va en porcentajes, Botsito y Colossus", async () => {
    renderIt();
    const table = await screen.findByRole("table", { name: /resultado por bot/i });
    expect(within(table).getByRole("rowheader", { name: "Botsito" })).toBeInTheDocument();
    expect(within(table).getByRole("rowheader", { name: "Colossus" })).toBeInTheDocument();
    // 18 de 42 episodios pasan; 13 en alerta; 9 fallan (el mismo fixture para los dos bots).
    await waitFor(() => expect(within(table).getAllByText("42,9 %")).toHaveLength(2));
    expect(within(table).getAllByText("31 %")).toHaveLength(2);
    expect(within(table).getAllByText("21,4 %")).toHaveLength(2);
  });

  it("dice el cumplimiento de cada etapa por bot", async () => {
    renderIt();
    const table = await screen.findByRole("table", { name: /cumplimiento por etapa/i });
    const row = await within(table).findByRole("row", { name: /descubrimiento/i });
    expect(within(row).getAllByText("83,3 %")).toHaveLength(2);
    expect(within(table).getByRole("row", { name: /variantes/i })).toHaveTextContent("86,3 %");
  });

  it("dónde terminan los episodios va con Botsito y, al lado, Colossus", async () => {
    renderIt();
    const funnel = await screen.findByRole("region", { name: /dónde terminan los episodios/i });
    const botsito = await within(funnel).findByRole("heading", { name: "Botsito" });
    const colossus = within(funnel).getByRole("heading", { name: "Colossus" });
    expect(before(botsito, colossus)).toBe(true);
    await waitFor(() => expect(called("/checks/stats?days=56&bot=actual")).toBe(true));
    expect(called("/checks/stats?days=56&bot=nuevo")).toBe(true);
  });

  it("la semana a semana se filtra por bot", async () => {
    renderIt();
    const trend = await screen.findByRole("region", { name: /cumplimiento por check, semana a semana/i });
    const picker = within(trend).getByRole("radiogroup", { name: /bot del cumplimiento por check/i });
    expect(within(picker).getByRole("radio", { name: "Los dos" })).toBeChecked();
    fireEvent.click(within(picker).getByRole("radio", { name: "Colossus" }));
    expect(within(picker).getByRole("radio", { name: "Colossus" })).toBeChecked();
    expect(await within(trend).findByText(/solo los episodios de colossus/i)).toBeInTheDocument();
  });

  it("la tendencia de calidad (antes en métricas legadas) se filtra por bot", async () => {
    renderIt();
    const trend = await screen.findByRole("region", { name: /tendencia de calidad/i });
    expect(await within(trend).findByText("tono")).toBeInTheDocument();
    await waitFor(() => expect(called("/evals/history?days=30&suite=online")).toBe(true));
    fireEvent.click(within(trend).getByRole("radio", { name: "Botsito" }));
    await waitFor(() => expect(called("/evals/history?days=30&suite=online&bot=actual")).toBe(true));
    await waitFor(() => expect(called("/evals/conversations?days=30&suite=online&bot=actual")).toBe(true));
  });

  it("explica el vacío cuando todavía no hay episodios calificados", async () => {
    statsPayload = { episodes: 0 };
    renderIt();
    expect((await screen.findAllByText(/aún no hay episodios calificados/i)).length).toBeGreaterThan(0);
  });
});

describe("Calidad LLM: la sección de cada bot", () => {
  it("Botsito: su ficha dice que no tenía clasificador y decidía con código", async () => {
    renderIt();
    const specs = await openBot("Botsito");
    expect(within(specs).getByText(/sin clasificador/i)).toBeInTheDocument();
    expect(within(specs).getAllByText(/código/i).length).toBeGreaterThan(0);
  });

  it("Colossus: su ficha dice la versión del motor de decisiones que corre", async () => {
    renderIt();
    const specs = await openBot("Colossus");
    expect(await within(specs).findByText(/ventas-2/)).toBeInTheDocument();
    expect(within(specs).getByText(/versión 2/i)).toBeInTheDocument();
    expect(within(specs).getByText(/jev 1\.13/i)).toBeInTheDocument();
    expect(within(specs).getByText(/otra versión del paquete/i)).toBeInTheDocument();
    await waitFor(() => expect(called("/api/agents/perception/engine")).toBe(true));
  });

  it("dentro de Colossus, cada gráfica muestra solo a Colossus", async () => {
    renderIt();
    await openBot("Colossus");
    const table = await screen.findByRole("table", { name: /resultado por bot/i });
    expect(within(table).getByRole("rowheader", { name: "Colossus" })).toBeInTheDocument();
    expect(within(table).queryByRole("rowheader", { name: "Botsito" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /dónde terminan los episodios/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /cumplimiento por check, semana a semana/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /tendencia de calidad/i })).toBeInTheDocument();
    // Ya filtradas: sin selector de bot adentro.
    expect(screen.queryByRole("radiogroup")).not.toBeInTheDocument();
    await waitFor(() => expect(called("/evals/history?days=30&suite=online&bot=nuevo")).toBe(true));
    await waitFor(() => expect(called("/scorecards?days=56&bot=nuevo")).toBe(true));
  });

  it("dentro de Botsito, la tendencia de calidad es la de Botsito", async () => {
    renderIt();
    await openBot("Botsito");
    const table = await screen.findByRole("table", { name: /resultado por bot/i });
    expect(within(table).queryByRole("rowheader", { name: "Colossus" })).not.toBeInTheDocument();
    await waitFor(() => expect(called("/evals/history?days=30&suite=online&bot=actual")).toBe(true));
  });

  it("Colossus dice cómo anduvo Jev en producción", async () => {
    renderIt();
    await openBot("Colossus");
    const jev = await screen.findByRole("region", { name: /jev en producción/i });
    expect(await within(jev).findByText(/50 % de las preguntas/i)).toBeInTheDocument();
    expect(within(jev).getByText(/1 de 2 decisiones cayeron a la regla porque jev falló/i)).toBeInTheDocument();
    await waitFor(() => expect(called("/api/agents/evals/production/jev?days=56&bot=nuevo")).toBe(true));
  });

  it("el código de un check de la matriz explica qué califica", async () => {
    renderIt();
    await openBot("Colossus");
    const table = await screen.findByRole("table", { name: /matriz de cumplimiento/i });
    fireEvent.click(await within(table).findByRole("button", { name: /^APE-01/ }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: /APE-01 · Saludo por hora y marca en el primer contacto/i })).toBeInTheDocument();
    expect(within(dialog).getByText(/si falla, la conversación queda en alerta/i)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Colossus" })).toHaveAttribute("aria-selected", "true");
  });

  it("una falla de la matriz lleva al turno que la tiene, en las conversaciones de ese bot", async () => {
    renderIt();
    await openBot("Colossus");
    const table = await screen.findByRole("table", { name: /matriz de cumplimiento/i });
    const firstRow = within(table).getAllByRole("row").filter((r) => r.closest("tbody"))[0];
    fireEvent.click(within(firstRow).getByRole("button", { name: /DES-05 · falla/i }));

    expect(screen.getByRole("tab", { name: /conversaciones/i })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("radio", { name: "Colossus" })).toBeChecked();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Hilo del turno 3")).toBeInTheDocument();
  });

  it("una fila de la matriz abre esa conversación", async () => {
    renderIt();
    await openBot("Colossus");
    const table = await screen.findByRole("table", { name: /matriz de cumplimiento/i });
    fireEvent.click(within(table).getAllByRole("row").filter((r) => r.closest("tbody"))[0]);

    expect(screen.getByRole("tab", { name: /conversaciones/i })).toHaveAttribute("aria-selected", "true");
    const list = await screen.findByRole("list", { name: /conversaciones calificadas/i });
    expect(within(list).getByRole("button", { name: /cliente ···0001/i })).toHaveAttribute("aria-current", "true");
  });

  it("si el servidor no filtró por bot, lo dice (API sin desplegar)", async () => {
    renderIt();
    await openBot("Colossus");
    expect(await screen.findByText(/el servidor no filtró por bot/i)).toBeInTheDocument();
  });

  it("el vacío dice de qué bot", async () => {
    statsPayload = { episodes: 0 };
    renderIt();
    await openBot("Colossus");
    expect((await screen.findAllByText(/aún no hay episodios de colossus/i)).length).toBeGreaterThan(0);
  });
});

describe("Calidad LLM: Conversaciones como el laboratorio", () => {
  it("lista las conversaciones reales con su resultado y su bot", async () => {
    renderIt();
    const list = await openConversations();
    const first = within(list).getByRole("button", { name: /cliente ···0001/i });
    expect(within(first).getByText("FALLA")).toBeInTheDocument();
    expect(within(first).getByText("Colossus")).toBeInTheDocument();
    expect(within(list).getByRole("button", { name: /cliente ···0005/i })).toBeInTheDocument();
  });

  it("trae su propio filtro: Botsito o Colossus", async () => {
    renderIt();
    await openConversations();
    const picker = screen.getByRole("radiogroup", { name: /bot de las conversaciones/i });
    expect(within(picker).getByRole("radio", { name: "Los dos" })).toBeChecked();
    fireEvent.click(within(picker).getByRole("radio", { name: "Colossus" }));
    await waitFor(() => expect(called("/production/conversations?days=56&bot=nuevo")).toBe(true));
    fireEvent.click(within(picker).getByRole("radio", { name: "Botsito" }));
    await waitFor(() => expect(called("/production/conversations?days=56&bot=actual")).toBe(true));
  });

  it("si el servidor no filtró por bot, lo dice", async () => {
    renderIt();
    await openConversations();
    fireEvent.click(screen.getByRole("radio", { name: "Colossus" }));
    expect(await screen.findByText(/el servidor no filtró por bot/i)).toBeInTheDocument();
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
  it("conserva calibración y goldens", async () => {
    renderIt();
    fireEvent.click(screen.getByRole("tab", { name: /goldens/i }));
    expect(await screen.findByText("Candidatos a golden")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /calibración/i }));
    expect(await screen.findByRole("table", { name: /calibración del juez/i })).toBeInTheDocument();
  });
});
