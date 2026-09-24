import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { RunLauncher } from "./RunLauncher";

/**
 * Botón "Nueva corrida" (plan §3.7, diseño §09): abre un formulario con los
 * bots (A1 siempre), las repeticiones y el banco; el costo se recalcula con
 * lo marcado y "Lanzar corrida" se apaga si pasa un tope. Con una corrida en
 * curso muestra el avance y "Cancelar corrida" pide confirmar (dos pasos,
 * sin diálogos nativos).
 */

const RUN = "run-20260923-1041-ab12";
const fetchMock = vi.fn();
let active: unknown = { active: null };
let estimate: Record<string, unknown> = {};
let launchResponse: { status: number; body: unknown } = { status: 202, body: { run_id: RUN, workflow_id: "lab-launch" } };

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

function estimateFor(url: string) {
  const params = new URL(url, "http://x").searchParams;
  const arms = (params.get("arms") ?? "").split(",");
  const reps = Number(params.get("reps"));
  const usd = arms.length * reps * 10.5;
  return {
    bench_id: params.get("bench") === "new" ? null : params.get("bench"),
    turns: 309,
    arms: [],
    reps,
    estimate_usd: usd,
    run_cap_usd: 120,
    month_cap_usd: 300,
    month_spent_usd: 0,
    month_left_usd: 300,
    fits: usd <= 60,
    reason: usd <= 60 ? null : "run_cap",
    spend_limit_usd: 120,
    ...estimate,
  };
}

beforeEach(() => {
  active = { active: null };
  estimate = {};
  launchResponse = { status: 202, body: { run_id: RUN, workflow_id: "lab-launch" } };
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.includes("/api/lab/runs/active/cancel")) return json({ cancel_requested: true, run_id: RUN }, 202);
    if (u.includes("/api/lab/runs/active")) return json(active);
    if (u.includes("/api/lab/estimate")) return json(estimateFor(u));
    if (u.endsWith("/api/lab/runs") && init?.method === "POST") return json(launchResponse.body, launchResponse.status);
    return json({}, 404);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function renderLauncher(lastBench: string | null = "bench-run-20260920-0900-cd34") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RunLauncher lastBenchId={lastBench} />
    </QueryClientProvider>,
  );
}

function estimateCalls() {
  return fetchMock.mock.calls.map(([u]) => String(u)).filter((u) => u.includes("/api/lab/estimate"));
}

describe("RunLauncher", () => {
  it("el panel está cerrado y no estima hasta abrirlo", async () => {
    renderLauncher();

    const open = await screen.findByRole("button", { name: "Nueva corrida" });
    expect(open).toHaveAttribute("aria-expanded", "false");
    expect(estimateCalls()).toHaveLength(0);
  });

  it("al abrir estima con A1 + los bots marcados, 1 repetición y banco nuevo", async () => {
    renderLauncher();
    fireEvent.click(await screen.findByRole("button", { name: "Nueva corrida" }));

    expect(screen.getByRole("checkbox", { name: /A1 · Actual simulado/ })).toBeDisabled();
    expect(await screen.findByText(/≈ US\$31,5 estimado · 309 turnos del banco/)).toBeInTheDocument();
    expect(estimateCalls().at(-1)).toContain("arms=A1%2CB%2CC&reps=1&bench=new");
  });

  it("recalcula al cambiar bots, repeticiones y banco", async () => {
    renderLauncher();
    fireEvent.click(await screen.findByRole("button", { name: "Nueva corrida" }));
    fireEvent.click(screen.getByRole("checkbox", { name: /C · Nuevo \+ OpenAI/ }));
    fireEvent.click(screen.getByRole("button", { name: "3 · decisión" }));
    fireEvent.click(screen.getByRole("button", { name: /Reusar el último/ }));

    await waitFor(() => expect(estimateCalls().at(-1)).toContain("arms=A1%2CB&reps=3&bench=bench-run-20260920-0900-cd34"));
  });

  it("si pasa un tope, dice cuál y no deja lanzar", async () => {
    renderLauncher();
    fireEvent.click(await screen.findByRole("button", { name: "Nueva corrida" }));
    fireEvent.click(screen.getByRole("button", { name: "3 · decisión" }));

    expect(await screen.findByText("Pasa el tope por corrida (US$120).")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lanzar corrida" })).toBeDisabled();
  });

  it("lanzar manda lo marcado", async () => {
    renderLauncher();
    fireEvent.click(await screen.findByRole("button", { name: "Nueva corrida" }));
    await screen.findByText(/≈ US\$31,5/);
    fireEvent.click(screen.getByRole("button", { name: "Lanzar corrida" }));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([u, init]) => String(u).endsWith("/api/lab/runs") && init?.method === "POST");
      expect(post && JSON.parse(post[1].body)).toEqual({ arms: ["A1", "B", "C"], reps: 1, bench: "new" });
    });
  });

  it("si ya hay una corrida, lo dice", async () => {
    launchResponse = { status: 409, body: { detail: { message: "Ya hay una corrida en curso." } } };
    renderLauncher();
    fireEvent.click(await screen.findByRole("button", { name: "Nueva corrida" }));
    await screen.findByText(/≈ US\$31,5/);
    fireEvent.click(screen.getByRole("button", { name: "Lanzar corrida" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Ya hay una corrida en curso.");
  });

  it("un timeout no afirma que falló: puede haberse lanzado", async () => {
    launchResponse = { status: 504, body: { detail: "El provider no respondió a tiempo: la operación PUEDE haberse aplicado." } };
    renderLauncher();
    fireEvent.click(await screen.findByRole("button", { name: "Nueva corrida" }));
    await screen.findByText(/≈ US\$31,5/);
    fireEvent.click(screen.getByRole("button", { name: "Lanzar corrida" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("No sé si la corrida arrancó");
  });

  it("con una corrida en curso muestra el avance, y cancelar pide confirmar", async () => {
    active = { active: { phase: "running", run_id: RUN, turns_done: 103, turns_total: 309, spent_usd: 4.1, estimate_usd: 12, arms: ["A1", "B"], reps: 1 } };
    renderLauncher();

    fireEvent.click(await screen.findByRole("button", { name: /Corrida en curso/ }));
    expect(screen.getByText("103 de 309 turnos · US$4,1 gastados de ≈ US$12")).toBeInTheDocument();
    expect(screen.getByText("Correr bots")).toHaveAttribute("aria-current", "step");

    fireEvent.click(screen.getByRole("button", { name: "Cancelar corrida" }));
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/cancel"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Sí, cancelar" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/lab/runs/active/cancel"))).toBe(true));
  });
});
