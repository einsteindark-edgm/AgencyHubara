import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

const BENCHES = ["caso-cortesia-1001", "caso-4148-real", "bench-run-20260920-0900-cd34"];

function renderLauncher(benches: string[] = BENCHES) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RunLauncher benches={benches} />
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

    expect(screen.getByRole("checkbox", { name: "Bot actual simulado (siempre va: es el control)" })).toBeDisabled();
    // Sin códigos de bot a la vista (el operador no entiende «A1 ·» ni «B0 ·»).
    expect(screen.queryByText(/\b(A1|B0|B) ·/)).toBeNull();
    expect(await screen.findByText(/≈ US\$21 estimado · 309 turnos del banco/)).toBeInTheDocument();
    expect(estimateCalls().at(-1)).toContain("arms=A1%2CB&reps=1&bench=new");
    // Sin el rival OpenAI (2026-09-28): el único bot nuevo es Jev.
    expect(screen.queryByRole("checkbox", { name: /OpenAI/ })).not.toBeInTheDocument();
  });

  it("ofrece B0 (el workflow nuevo con las reglas de hoy) sin marcar, y lo manda si se marca", async () => {
    renderLauncher();
    fireEvent.click(await screen.findByRole("button", { name: "Nueva corrida" }));
    const b0 = screen.getByRole("checkbox", { name: "Bot nuevo sin Jev (prueba: tiene que dar lo mismo que el actual)" });
    expect(b0).not.toBeChecked();

    fireEvent.click(b0);

    await waitFor(() => expect(estimateCalls().at(-1)).toContain("arms=A1%2CB0%2CB&reps=1&bench=new"));
  });

  it("recalcula al cambiar bots, repeticiones y banco", async () => {
    renderLauncher();
    fireEvent.click(await screen.findByRole("button", { name: "Nueva corrida" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Bot nuevo con Jev" }));
    fireEvent.click(screen.getByRole("button", { name: "3 veces (para decidir)" }));
    fireEvent.click(screen.getByRole("button", { name: "Repetir un banco guardado" }));

    await waitFor(() => expect(estimateCalls().at(-1)).toContain("arms=A1&reps=3&bench=caso-cortesia-1001"));
  });

  it("deja escoger cualquier banco guardado, no solo el de la última corrida", async () => {
    renderLauncher();
    fireEvent.click(await screen.findByRole("button", { name: "Nueva corrida" }));
    fireEvent.click(screen.getByRole("button", { name: "Repetir un banco guardado" }));

    const picker = screen.getByRole("combobox", { name: "Banco" });
    expect(within(picker).getAllByRole("option").map((o) => o.textContent)).toEqual(BENCHES);
    fireEvent.change(picker, { target: { value: "caso-4148-real" } });

    await waitFor(() => expect(estimateCalls().at(-1)).toContain("bench=caso-4148-real"));
    await screen.findByText(/≈ US\$21/);
    fireEvent.click(screen.getByRole("button", { name: "Lanzar corrida" }));
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([u, init]) => String(u).endsWith("/api/lab/runs") && init?.method === "POST");
      expect(post && JSON.parse(post[1].body)).toEqual({ arms: ["A1", "B"], reps: 1, bench: "caso-4148-real" });
    });
  });

  it("sin bancos guardados, repetir está apagado y no hay selector", async () => {
    renderLauncher([]);
    fireEvent.click(await screen.findByRole("button", { name: "Nueva corrida" }));

    expect(screen.getByRole("button", { name: "Repetir un banco guardado (no hay)" })).toBeDisabled();
    expect(screen.queryByRole("combobox", { name: "Banco" })).toBeNull();
  });

  it("si pasa un tope, dice cuál y no deja lanzar", async () => {
    renderLauncher();
    fireEvent.click(await screen.findByRole("button", { name: "Nueva corrida" }));
    fireEvent.click(screen.getByRole("button", { name: "3 veces (para decidir)" }));

    expect(await screen.findByText("Pasa el tope por corrida (US$120).")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lanzar corrida" })).toBeDisabled();
  });

  it("lanzar manda lo marcado", async () => {
    renderLauncher();
    fireEvent.click(await screen.findByRole("button", { name: "Nueva corrida" }));
    await screen.findByText(/≈ US\$21/);
    fireEvent.click(screen.getByRole("button", { name: "Lanzar corrida" }));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([u, init]) => String(u).endsWith("/api/lab/runs") && init?.method === "POST");
      expect(post && JSON.parse(post[1].body)).toEqual({ arms: ["A1", "B"], reps: 1, bench: "new" });
    });
  });

  it("con otro paquete de decisión en la imagen, deja probarlo junto al de la tienda (F6)", async () => {
    estimate = {
      bundles: [
        { id: "ventas", version: 1, active: true },
        { id: "ventas-2", version: 2, active: false },
      ],
      bundle_arms: ["B0", "B"],
    };
    renderLauncher();
    fireEvent.click(await screen.findByRole("button", { name: "Nueva corrida" }));

    const picker = await screen.findByRole("combobox", { name: "Paquete a comparar" });
    expect(within(picker).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Ninguno (solo el de la tienda: ventas, versión 1)",
      "ventas-2 (versión 2)",
    ]);
    fireEvent.change(picker, { target: { value: "ventas-2" } });

    await waitFor(() => expect(estimateCalls().at(-1)).toContain("arms=A1%2CB%2CB%40ventas-2"));
    await screen.findByText(/≈ US\$31/);
    fireEvent.click(screen.getByRole("button", { name: "Lanzar corrida" }));
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([u, init]) => String(u).endsWith("/api/lab/runs") && init?.method === "POST");
      expect(post && JSON.parse(post[1].body)).toEqual({ arms: ["A1", "B", "B@ventas-2"], reps: 1, bench: "new" });
    });
  });

  it("con un solo paquete de decisión, dice cuál corre y no ofrece comparar", async () => {
    estimate = { bundles: [{ id: "ventas", version: 1, active: true }], bundle_arms: ["B0", "B"] };
    renderLauncher();
    fireEvent.click(await screen.findByRole("button", { name: "Nueva corrida" }));

    expect(await screen.findByText("ventas, versión 1 (el de la tienda). Para comparar otro, primero hay que subirlo.")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Paquete a comparar" })).toBeNull();
  });

  it("si ya hay una corrida, lo dice", async () => {
    launchResponse = { status: 409, body: { detail: { message: "Ya hay una corrida en curso." } } };
    renderLauncher();
    fireEvent.click(await screen.findByRole("button", { name: "Nueva corrida" }));
    await screen.findByText(/≈ US\$21/);
    fireEvent.click(screen.getByRole("button", { name: "Lanzar corrida" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Ya hay una corrida en curso.");
  });

  it("un timeout no afirma que falló: puede haberse lanzado", async () => {
    launchResponse = { status: 504, body: { detail: "El provider no respondió a tiempo: la operación PUEDE haberse aplicado." } };
    renderLauncher();
    fireEvent.click(await screen.findByRole("button", { name: "Nueva corrida" }));
    await screen.findByText(/≈ US\$21/);
    fireEvent.click(screen.getByRole("button", { name: "Lanzar corrida" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("No sé si la corrida arrancó");
  });

  it("con una corrida en curso muestra el avance, y cancelar pide confirmar", async () => {
    active = { active: { phase: "running", run_id: RUN, turns_done: 103, turns_total: 309, spent_usd: 4.1, estimate_usd: 12, arms: ["A1", "B"], reps: 1 } };
    renderLauncher();

    fireEvent.click(await screen.findByRole("button", { name: /Corrida en curso/ }));
    expect(screen.getByText("103 de 309 turnos · US$4,1 gastados de ≈ US$12")).toBeInTheDocument();
    expect(screen.getByText("Correr los bots")).toHaveAttribute("aria-current", "step");

    fireEvent.click(screen.getByRole("button", { name: "Cancelar corrida" }));
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/cancel"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Sí, cancelar" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/lab/runs/active/cancel"))).toBe(true));
  });
});
