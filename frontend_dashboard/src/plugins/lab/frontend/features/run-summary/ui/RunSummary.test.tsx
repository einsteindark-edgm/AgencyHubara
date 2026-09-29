import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { LabRun } from "@plugins/lab/frontend/entities/lab-run";

import { RunSummary } from "./RunSummary";

/**
 * Pestaña Resumen del laboratorio (plan §5 y §11; revisión 2026-09-29: el
 * operador no entendía «IC 95 %», «pp», «Holm», «F1», «Brier» ni «vara»).
 * Responde, en este orden: ¿el bot nuevo es mejor?, cómo le fue a cada bot,
 * en qué se diferencian (cada check por nombre), qué turnos cambiaron, si se
 * puede confiar en la corrida y cómo anduvo Jev. Las gráficas de Calidad LLM
 * quedan plegadas; el detalle estadístico, también.
 */

const RUN = "run-20260924-0930-ab12";
const SID = "wa_573001234567";

const run: LabRun = {
  run_id: RUN, bench_id: "bench-x", arms: ["A0", "A1", "B"], reps: 1, registry_version: 4, counts: {}, phase: "done",
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
      profile: "jev-v3",
      metrics: [{ rep: 0, turns: 300, errors: 1, perception: { turns: 300, fallback_rate: 0.004, p50_ms: 260, p95_ms: 480 },
        verify: null, complement_rate: 0.05, extra_round_rate: 0.02, cost_per_turn_usd: 0.019, perception_cost_per_turn_usd: 0.0002 }],
      topics: { turns: 120, precision: 0.9, recall: 0.8, f1: 0.85, calibration: { n: 400, brier: 0.08, ece: 0.04 } },
    },
  },
  judge: { used: true, errors: 0 },
  diffs: ["A0:A1", "A1:B"],
};

function week(applicable: number, passed: number) {
  return [{ week: "2026-09-28", applicable, passed, rate: applicable ? passed / applicable : null }];
}

function summary(pasa: number, fails: { invented: number; greeting: number }) {
  return {
    reps: 1, mode: "turn", episodes: 3, verdicts: { FALLA: 0, ALERTA: 3 - pasa, PASA: pasa, SIN_DATOS: 0 },
    pareto: [{ check_id: "DES-06", name: "Sin datos de catálogo inventados", level: "critico", failures: fails.invented }],
    trend: [
      { check_id: "DES-06", name: "Sin datos de catálogo inventados", level: "critico", weeks: week(3, 3 - fails.invented) },
      { check_id: "APE-01", name: "Saludo por hora y marca en el primer contacto", level: "mayor", weeks: week(3, 3 - fails.greeting) },
      { check_id: "EST-01", name: "Un check que nadie falló", level: "menor", weeks: week(3, 3) },
    ],
    funnel: [{ stage: "cierre", FALLA: 0, ALERTA: 3 - pasa, PASA: pasa, SIN_DATOS: 0 }],
    pass_k: { k: 1, episodes: 3, rate: pasa / 3 },
  };
}

const DIFF = {
  base: "A1", cand: "B",
  episode_pass: { delta: 0.05, low: -0.03, high: 0.12, conclusive: false, sessions: 80 },
  pass_k: null,
  checks: [{ check_id: "EST-08", delta: 0.2, low: 0.1, high: 0.3, conclusive: true, sessions: 40 }],
  changed_turns: [{ session_id: SID, episode_id: "ep_1", turn: 2, base: "FALLA", cand: "PASA", checks: ["EST-08"] }],
};

const CATALOG = {
  registry_version: 4,
  checks: [{ id: "EST-08", name: "Responde lo que el cliente preguntó", level: "mayor", kind: "judge", rule: "…" }],
};

const fetchMock = vi.fn();

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

function routes(diff = DIFF) {
  fetchMock.mockImplementation((url: string) => {
    const u = String(url);
    if (u.endsWith(`/runs/${RUN}/report`)) return json(REPORT);
    if (u.endsWith("/api/lab/checks")) return json(CATALOG);
    if (u.includes(`/runs/${RUN}/summary?arm=A0`)) return json(summary(0, { invented: 3, greeting: 1 }));
    if (u.includes(`/runs/${RUN}/summary?arm=A1`)) return json(summary(1, { invented: 3, greeting: 0 }));
    if (u.includes(`/runs/${RUN}/summary?arm=B`)) return json(summary(2, { invented: 2, greeting: 1 }));
    if (u.includes(`/runs/${RUN}/diff?base=A1&cand=B`)) return json(diff);
    return json({ detail: "no" }, 404);
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  routes();
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
  it("arriba responde si el bot nuevo es mejor, en palabras; el detalle estadístico queda plegado", async () => {
    renderTab();

    const answer = await screen.findByRole("region", { name: "¿El bot nuevo es mejor?" });
    expect(
      await within(answer).findByText("Todavía no se puede saber: con estas conversaciones la diferencia puede ser casualidad."),
    ).toBeInTheDocument();
    expect(within(answer).getByText("Detalle estadístico")).toBeVisible();
    expect(within(answer).getByText(/Diferencia en conversaciones que pasan.*IC 95 %/)).not.toBeVisible();
  });

  it("con pocas conversaciones en común dice cuántas hacen falta", async () => {
    routes({ ...DIFF, episode_pass: { delta: 0.33, low: 0, high: 0.66, conclusive: false, sessions: 3 } });
    renderTab();

    const answer = await screen.findByRole("region", { name: "¿El bot nuevo es mejor?" });
    expect(
      await within(answer).findByText("Todavía no se puede saber: 3 conversaciones en común son pocas (hacen falta al menos 15)."),
    ).toBeInTheDocument();
  });

  it("dice cómo le fue a cada bot: cuántas conversaciones pasan, quedan en alerta o fallan", async () => {
    renderTab();

    const table = await screen.findByRole("table", { name: "Resultado por bot" });
    const fresh = await within(table).findByRole("row", { name: /Bot nuevo con Jev/ });
    expect(within(fresh).getAllByRole("cell").map((c) => c.textContent)).toEqual(["3", "2", "1", "0", "0"]);
    expect(within(table).getByRole("row", { name: /Producción/ })).toBeInTheDocument();
  });

  it("en qué se diferencian: cada check por su nombre con cuántas veces falló con cada bot", async () => {
    renderTab();

    const table = await screen.findByRole("table", { name: "En qué se diferencian los bots" });
    const invented = await within(table).findByRole("row", { name: /Sin datos de catálogo inventados/ });
    expect(within(invented).getAllByRole("cell").map((c) => c.textContent)).toEqual(["3 de 3", "3 de 3", "2 de 3"]);
    const greeting = within(table).getByRole("row", { name: /Saludo por hora y marca/ });
    expect(within(greeting).getAllByRole("cell").map((c) => c.textContent)).toEqual(["1 de 3", "0 de 3", "1 de 3"]);
    expect(within(table).queryByText("Un check que nadie falló")).toBeNull();
    expect(within(table).queryByText("DES-06")).toBeNull();
  });

  it("los turnos que cambiaron dicen qué pasó en palabras y abren la conversación", async () => {
    const open = renderTab();

    const changed = await screen.findByRole("list", { name: "Turnos que cambiaron" });
    const item = await within(changed).findByRole("listitem");
    expect(item).toHaveTextContent("Cliente ···4567 · turno 2");
    expect(item).toHaveTextContent("Bot actual simulado: FALLA → Bot nuevo con Jev: PASA");
    expect(await within(item).findByText("Responde lo que el cliente preguntó")).toBeInTheDocument();
    fireEvent.click(within(item).getByRole("button", { name: "Ver conversación" }));
    expect(open).toHaveBeenCalledWith(SID);
  });

  it("dice si se puede confiar en la corrida, en palabras", async () => {
    renderTab();

    const trust = await screen.findByRole("region", { name: "¿Se puede confiar en esta corrida?" });
    expect(trust).toHaveTextContent("El simulador reproduce bien a producción: coincide en 94 % de los checks (mínimo exigido: 90 %).");
    expect(trust).toHaveTextContent("Calificada con reglas automáticas y con el juez.");
    expect(trust).not.toHaveTextContent("vara");
  });

  it("cuenta cómo anduvo Jev, en palabras", async () => {
    renderTab();

    const jev = await screen.findByRole("region", { name: "Jev en esta corrida" });
    expect(jev).toHaveTextContent("jev-v3");
    expect(jev).toHaveTextContent("El 95 % de las respuestas de Jev llegó en menos de 0,5 s");
    expect(jev).toHaveTextContent("0,4 %");
    expect(jev).toHaveTextContent("US$0,019");
    expect(jev).toHaveTextContent("85 %");
    expect(jev).not.toHaveTextContent("Brier");
  });

  it("las gráficas de Calidad LLM quedan plegadas y sin la tendencia semanal (una corrida no tiene semanas)", async () => {
    renderTab();

    expect(await screen.findByText("Gráficas de Calidad LLM (por bot)")).toBeVisible();
    expect(screen.queryByText("Cumplimiento por check, semana a semana")).toBeNull();
  });

  it("un bot que la corrida no alcanzó a simular queda pendiente, no como falla", async () => {
    fetchMock.mockImplementation((url: string) => {
      const u = String(url);
      if (u.endsWith(`/runs/${RUN}/report`)) return json({ ...REPORT, arms_pending: ["Z"] });
      return json({ detail: "no" }, 404);
    });
    renderTab();

    expect(await screen.findByText(/Z: la corrida no alcanzó a simularlo/)).toBeInTheDocument();
  });

  it("una corrida sin resumen lo dice", async () => {
    fetchMock.mockImplementation(() => json({ detail: "La corrida no publicó su resumen." }, 404));
    renderTab();

    expect(await screen.findByText("Esta corrida todavía no publicó su resumen.")).toBeInTheDocument();
  });
});
