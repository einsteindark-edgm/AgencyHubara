import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import statsFixture from "@plugins/agents_admin/frontend/entities/check-stats/fixtures/check-stats.json";
import calibrationFixture from "@plugins/agents_admin/frontend/entities/eval-label/fixtures/calibration.json";
import labelsFixture from "@plugins/agents_admin/frontend/entities/eval-label/fixtures/labels.json";
import queueFixture from "@plugins/agents_admin/frontend/entities/eval-label/fixtures/labels-queue.json";
import checksFixture from "@plugins/agents_admin/frontend/entities/scorecard/fixtures/checks.json";
import detailFixture from "@plugins/agents_admin/frontend/entities/scorecard/fixtures/scorecard-detail.json";
import listFixture from "@plugins/agents_admin/frontend/entities/scorecard/fixtures/scorecards.json";

import { AgentsQuality } from "./AgentsQuality";

const fetchMock = vi.fn();
let statsPayload: unknown = statsFixture;

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

/** Router de fetch por endpoint (orden: rutas más específicas primero). */
const ROUTES: Array<[string, () => unknown]> = [
  ["/api/agents/evals/checks/stats", () => statsPayload],
  ["/api/agents/evals/checks", () => checksFixture],
  ["/api/agents/evals/scorecards", () => listFixture],
  ["/api/agents/evals/scorecard?", () => detailFixture],
  ["/api/agents/evals/labels/queue", () => queueFixture],
  ["/api/agents/evals/labels?", () => labelsFixture],
  ["/api/agents/evals/calibration", () => calibrationFixture],
];

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

describe("AgentsQuality (scorecard por etapa)", () => {
  it("abre en Resumen con veredictos, Pareto, embudo y tendencia", async () => {
    renderIt();
    expect(screen.getByRole("tab", { name: /resumen/i })).toHaveAttribute("aria-selected", "true");
    const tiles = await screen.findByRole("list", { name: /veredictos de los episodios/i });
    expect(within(tiles).getByRole("button", { name: /falla: 9 episodios/i })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: /pareto de fallos/i })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /embudo de etapa terminal/i })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /^CON-01: cumplimiento semanal/ })).toBeInTheDocument();
  });

  it("explica el vacío cuando todavía no hay scorecards", async () => {
    statsPayload = { episodes: 0 };
    renderIt();
    expect(
      await screen.findByText(/aún no hay scorecards: se generan al cerrar cada episodio/i),
    ).toBeInTheDocument();
  });

  it("la alerta cuenta episodios en FALLA y lleva a Conversaciones filtradas", async () => {
    renderIt();
    const alert = await screen.findByRole("button", { name: /2 episodios para revisar/i });
    fireEvent.click(alert);
    expect(screen.getByRole("tab", { name: /conversaciones/i })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("button", { name: "Falla" })).toHaveAttribute("aria-pressed", "true");
    const table = await screen.findByRole("table", { name: /matriz de cumplimiento/i });
    expect(within(table).getAllByRole("row").filter((r) => r.closest("tbody"))).toHaveLength(2);
  });

  it("una barra del Pareto filtra la matriz a los episodios que fallan ese check", async () => {
    renderIt();
    fireEvent.click(await screen.findByRole("button", { name: /^VAR-01:/ }));
    expect(screen.getByRole("tab", { name: /conversaciones/i })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("button", { name: /quitar filtro VAR-01/i })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: /matriz de cumplimiento/i });
    expect(within(table).getAllByRole("row").filter((r) => r.closest("tbody"))).toHaveLength(1);
  });

  it("elegir un episodio muestra su tira y su scorecard, con el primer crítico seleccionado", async () => {
    renderIt();
    fireEvent.click(screen.getByRole("tab", { name: /conversaciones/i }));
    expect(await screen.findByText(/elige una conversación/i)).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: /matriz de cumplimiento/i });
    fireEvent.click(within(table).getAllByRole("row").filter((r) => r.closest("tbody"))[0]);
    expect(await screen.findByRole("group", { name: /tira de trayectoria/i })).toBeInTheDocument();
    const panel = screen.getByRole("complementary", { name: /scorecard del episodio/i });
    const rule = checksFixture.checks.find((c) => c.id === "VAR-01")!.rule;
    expect(within(panel).getByText(rule)).toBeInTheDocument();
    // Click en otro check de la tira cambia el detalle del panel.
    fireEvent.click(screen.getByRole("button", { name: /^CON-01 · .*falla crítica/ }));
    const con01 = checksFixture.checks.find((c) => c.id === "CON-01")!.rule;
    await waitFor(() => expect(within(panel).getByText(con01)).toBeInTheDocument());
  });

  it("conserva las métricas legadas y los goldens en sus pestañas", async () => {
    renderIt();
    fireEvent.click(screen.getByRole("tab", { name: /métricas legadas/i }));
    expect(await screen.findByText("Tendencia de calidad")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /goldens/i }));
    expect(await screen.findByText("Candidatos a golden")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /calibración/i }));
    expect(await screen.findByRole("table", { name: /calibración del juez/i })).toBeInTheDocument();
  });
});
