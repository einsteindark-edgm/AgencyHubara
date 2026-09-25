import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { LabRun } from "@plugins/lab/frontend/entities/lab-run";

import { RunSummary } from "./RunSummary";

/**
 * Pestaña Resumen del laboratorio (plan §5 y §11, PR 13): las MISMAS
 * gráficas de Calidad LLM por bot, la comparación contra el bot actual con
 * su intervalo, la fidelidad del simulador y la arena de los bots nuevos.
 */

const RUN = "run-20260924-0930-ab12";
const SID = "wa_573001234567";

const run: LabRun = {
  run_id: RUN, bench_id: "bench-x", arms: ["A0", "A1", "B"], reps: 3, registry_version: 4, counts: {}, phase: "done",
  turns_done: null, turns_total: null, spent_usd: null, error: null, notes: [], started_at_ms: null, updated_at_ms: null,
};

const REPORT = {
  run_id: RUN,
  mode: "turn",
  arms: ["A0", "A1", "B"],
  fidelity: { n: 400, agreement: 0.94, threshold: 0.9, ok: true },
  validation: { episodes: 91, prod_registry_versions: [3], agreement: 0.97, verdict_agreement: 0.81 },
  arena: {
    B: {
      profile: "jev-v1",
      metrics: [{ rep: 0, turns: 300, errors: 1, perception: { turns: 300, fallback_rate: 0.004, p50_ms: 260, p95_ms: 480 },
        verify: null, complement_rate: 0.05, extra_round_rate: 0.02, cost_per_turn_usd: 0.019, perception_cost_per_turn_usd: 0.0002 }],
      topics: { turns: 120, precision: 0.9, recall: 0.8, f1: 0.85, calibration: { n: 400, brier: 0.08, ece: 0.04 } },
    },
  },
  judge: { used: true, errors: 0 },
  diffs: ["A0:A1", "A1:B"],
};

function summary(pasa: number, passK = 0.2) {
  return {
    reps: 3, mode: "turn", episodes: 91, verdicts: { FALLA: 9, ALERTA: 91 - 9 - pasa, PASA: pasa, SIN_DATOS: 0 },
    pareto: [{ check_id: "EST-06", name: "Mecánica de la etapa", level: "mayor", failures: 12 }],
    trend: [], funnel: [{ stage: "cierre", FALLA: 3, ALERTA: 4, PASA: 5, SIN_DATOS: 0 }],
    pass_k: { k: 3, episodes: 91, rate: passK },
  };
}

const DIFF = {
  base: "A1", cand: "B",
  episode_pass: { delta: 0.05, low: -0.03, high: 0.12, conclusive: false, sessions: 80 },
  pass_k: { base: { k: 3, episodes: 91, rate: 0.2 }, cand: { k: 3, episodes: 91, rate: 0.26 } },
  checks: [{ check_id: "EST-08", delta: 0.2, low: 0.1, high: 0.3, conclusive: true, sessions: 40 }],
  changed_turns: [{ session_id: SID, episode_id: "ep_1", turn: 2, base: "FALLA", cand: "PASA", checks: ["EST-08"] }],
};

const fetchMock = vi.fn();

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

function urls(): string[] {
  return fetchMock.mock.calls.map(([u]) => String(u));
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((url: string) => {
    const u = String(url);
    if (u.endsWith(`/runs/${RUN}/report`)) return json(REPORT);
    if (u.includes(`/runs/${RUN}/summary?arm=A1`)) return json(summary(20));
    if (u.includes(`/runs/${RUN}/summary?arm=B`)) return json(summary(26, 0.26));
    if (u.includes(`/runs/${RUN}/diff?base=A1&cand=B`)) return json(DIFF);
    return json({ detail: "no" }, 404);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function renderTab(onOpenConversations = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RunSummary run={run} onOpenConversations={onOpenConversations} />
    </QueryClientProvider>,
  );
  return onOpenConversations;
}

describe("RunSummary", () => {
  it("dice si se puede confiar en la comparación: fidelidad, validación y juez", async () => {
    renderTab();

    const health = await screen.findByRole("region", { name: "Confianza de la corrida" });
    expect(health).toHaveTextContent("Fidelidad del simulador 94 %");
    expect(health).toHaveTextContent("vara 90 %");
    expect(health).toHaveTextContent("91 episodios");
    expect(health).toHaveTextContent("con juez");
  });

  it("muestra las gráficas de Calidad LLM del bot actual y cambia de bot", async () => {
    renderTab();

    expect(await screen.findByText("Qué arreglar primero")).toBeInTheDocument();
    expect(screen.getByText(/pasan en las 3 repeticiones: 20 %/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Nuevo + Jev" }));

    await waitFor(() => expect(urls().some((u) => u.includes("summary?arm=B"))).toBe(true));
    expect(await screen.findByText(/pasan en las 3 repeticiones: 26 %/)).toBeInTheDocument();
  });

  it("la comparación no declara ganador si el intervalo cruza el cero", async () => {
    renderTab();

    const cmp = await screen.findByRole("region", { name: "Comparación" });
    expect(await within(cmp).findByText("Aún no concluyente: el intervalo cruza el cero")).toBeInTheDocument();
    expect(cmp).toHaveTextContent("IC 95 %: −3 a +12 pp · 80 conversaciones");
    expect(within(cmp).getByRole("row", { name: /EST-08/ })).toHaveTextContent("+20 pp");
    expect(within(cmp).getByText("Cliente ···4567")).toBeInTheDocument();
  });

  it("la arena muestra latencia, caídas, costo y acuerdo con el juez de cada bot nuevo", async () => {
    renderTab();

    const arena = await screen.findByRole("region", { name: "Arena de clasificadores" });
    const row = within(arena).getByRole("row", { name: /Nuevo \+ Jev/ });
    expect(row).toHaveTextContent("jev-v1");
    expect(row).toHaveTextContent("480 ms");
    expect(row).toHaveTextContent("0,4 %");
    expect(row).toHaveTextContent("85 %");
  });

  it("la arena muestra el costo por turno con 4 decimales", async () => {
    renderTab();

    const arena = await screen.findByRole("region", { name: "Arena de clasificadores" });
    expect(within(arena).getByRole("row", { name: /Nuevo \+ Jev/ })).toHaveTextContent("US$0,019");
  });

  it("un bot que la corrida no alcanzó a simular queda pendiente, no como falla", async () => {
    fetchMock.mockImplementation((url: string) => {
      const u = String(url);
      if (u.endsWith(`/runs/${RUN}/report`)) return json({ ...REPORT, arms_pending: ["C"] });
      if (u.includes(`/runs/${RUN}/summary?arm=A1`)) return json(summary(20));
      return json({ detail: "no" }, 404);
    });
    renderTab();

    expect(await screen.findByText(/Nuevo \+ OpenAI: la corrida no alcanzó a simularlo/)).toBeInTheDocument();
  });

  it("el Pareto del laboratorio habla de la corrida, no de días", async () => {
    fetchMock.mockImplementation((url: string) => {
      const u = String(url);
      if (u.endsWith(`/runs/${RUN}/report`)) return json(REPORT);
      if (u.includes(`/runs/${RUN}/summary?arm=A1`)) return json({ ...summary(20), pareto: [] });
      return json({ detail: "no" }, 404);
    });
    renderTab();

    expect(await screen.findByText("Ningún check falló en esta corrida.")).toBeInTheDocument();
  });

  it("la tabla de checks dice qué hace falta para ser concluyente", async () => {
    renderTab();

    const cmp = await screen.findByRole("region", { name: "Comparación" });
    expect(await within(cmp).findByText(/al menos 15 conversaciones/)).toBeInTheDocument();
    expect(within(cmp).getByText(/comparaciones múltiples \(Holm\)/)).toBeInTheDocument();
  });

  it("elegir un veredicto lleva a las conversaciones", async () => {
    const open = renderTab();

    const tile = await screen.findByRole("button", { name: /Falla/ });
    fireEvent.click(tile);
    expect(open).toHaveBeenCalled();
  });

  it("una corrida sin resumen lo dice", async () => {
    fetchMock.mockImplementation(() => json({ detail: "La corrida no publicó su resumen." }, 404));
    renderTab();

    expect(await screen.findByText("Esta corrida todavía no publicó su resumen.")).toBeInTheDocument();
  });
});
