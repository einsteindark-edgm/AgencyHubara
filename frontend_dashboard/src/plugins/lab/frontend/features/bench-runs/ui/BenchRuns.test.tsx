import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { LabRun } from "@plugins/lab/frontend/entities/lab-run";

import { BenchRuns } from "./BenchRuns";

/**
 * Pestaña "Banco y corridas" (diseño §09): qué entró al banco de la corrida
 * elegida, qué se excluyó y por qué, y cada corrida con su estado y su costo.
 */

const RUN = "run-20260923-1041-ab12";
const fetchMock = vi.fn();

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

const runs: LabRun[] = [
  { run_id: RUN, bench_id: "bench-run-20260923-1041-ab12", arms: ["A0", "A1", "B"], reps: 1, registry_version: 3, counts: { sessions: 82, cases: 309 }, phase: "done", turns_done: 309, turns_total: 309, spent_usd: 7.4, error: null, notes: ["simulación pendiente"], started_at_ms: 1790178069000, updated_at_ms: 1790181669000 },
  { run_id: "run-20260920-0900-cd34", bench_id: "bench-run-20260920-0900-cd34", arms: ["A0"], reps: 1, registry_version: 3, counts: {}, phase: "failed", turns_done: null, turns_total: null, spent_usd: null, error: "la caja no arrancó", notes: [], started_at_ms: 1789911600000, updated_at_ms: null },
];

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((url: string) =>
    String(url).includes(`/api/lab/runs/${RUN}/bench`)
      ? json({
          bench_id: "bench-run-20260923-1041-ab12",
          counts: { sessions: 82, cases: 309, excluded_turns: 3 },
          exclusions: [
            { id: "wa_573001234567/ep_1/t3", reason: "turno_del_sistema" },
            { id: "wa_573007654321/ep_2/t1", reason: "turno_del_sistema" },
            { id: "wa_573000000099", reason: "sin_metadata" },
          ],
        })
      : json({}, 404),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function renderTab(onSelectRun = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <BenchRuns runs={runs} selectedRun={RUN} onSelectRun={onSelectRun} />
    </QueryClientProvider>,
  );
  return onSelectRun;
}

describe("BenchRuns", () => {
  it("resume el banco de la corrida elegida", async () => {
    renderTab();

    expect(await screen.findByText("82")).toBeInTheDocument();
    expect(screen.getByText("conversaciones")).toBeInTheDocument();
    expect(screen.getByText("309")).toBeInTheDocument();
    expect(screen.getByText("turnos del cliente en el banco")).toBeInTheDocument();
  });

  it("agrupa las exclusiones por motivo, sin mostrar teléfonos completos", async () => {
    renderTab();

    const table = await screen.findByRole("table", { name: "Exclusiones del banco" });
    expect(within(table).getByRole("row", { name: /Turno del sistema.*2/ })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: /Conversación sin metadata.*1/ })).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("573001234567");
  });

  it("lista las corridas con estado y costo; la elegida va marcada y otra se puede elegir", async () => {
    const onSelect = renderTab();
    const table = screen.getByRole("table", { name: "Corridas" });

    const current = within(table).getByRole("row", { name: new RegExp(RUN) });
    expect(current).toHaveAttribute("aria-current", "true");
    expect(current).toHaveTextContent("Terminada");
    expect(current).toHaveTextContent("US$7,4");
    const failed = within(table).getByRole("row", { name: /run-20260920-0900-cd34/ });
    expect(failed).toHaveTextContent("Falló: la caja no arrancó");

    fireEvent.click(within(failed).getByRole("button", { name: "Ver" }));
    expect(onSelect).toHaveBeenCalledWith("run-20260920-0900-cd34");
  });
});
