import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import LabPage from "./LabPage";

/**
 * Sección Laboratorio (plan §11, diseño §09): barra con la corrida elegida y
 * el botón "Nueva corrida"; pestañas Conversaciones y Banco y corridas.
 */

const RUN = "run-20260923-1041-ab12";
const fetchMock = vi.fn();
let runsResponse: { status: number; body: unknown } = { status: 200, body: { runs: [] } };

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

beforeEach(() => {
  runsResponse = { status: 200, body: { runs: [] } };
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((url: string) => {
    const u = String(url);
    if (u.endsWith("/api/lab/runs")) return json(runsResponse.body, runsResponse.status);
    if (u.endsWith("/api/lab/runs/active")) return json({ active: null });
    if (u.endsWith(`/runs/${RUN}/conversations`)) return json({ conversations: [] });
    if (u.endsWith(`/runs/${RUN}/bench`)) return json({ bench_id: "bench-x", counts: { sessions: 82, cases: 309, excluded_turns: 0 }, exclusions: [] });
    return json({}, 404);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <LabPage />
    </QueryClientProvider>,
  );
}

describe("LabPage", () => {
  it("sin corridas invita a lanzar la primera", async () => {
    renderPage();

    expect(await screen.findByText("Todavía no hay corridas. Usa Nueva corrida para lanzar la primera.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Nueva corrida" })).toBeInTheDocument();
  });

  it("si el laboratorio no está configurado en este entorno, lo dice", async () => {
    runsResponse = { status: 503, body: { detail: "El laboratorio no está configurado (falta LAB_BUCKET)." } };
    renderPage();

    expect(await screen.findByText("El laboratorio no está configurado (falta LAB_BUCKET).")).toBeInTheDocument();
  });

  it("si el API de este entorno no tiene el plugin lab prendido, lo explica en vez de un 404 crudo", async () => {
    runsResponse = { status: 404, body: { detail: "Not Found" } };
    renderPage();

    expect(await screen.findByText("El laboratorio no está habilitado en este entorno (el plugin lab está apagado en el API).")).toBeInTheDocument();
  });

  it("con una corrida: la muestra en la barra y cambia entre pestañas", async () => {
    runsResponse = { status: 200, body: { runs: [{ run_id: RUN, bench_id: "bench-x", arms: ["A0", "A1", "B"], reps: 3, phase: "done" }] } };
    renderPage();

    expect(await screen.findByRole("combobox", { name: "Corrida" })).toHaveValue(RUN);
    expect(screen.getByText("bench-x · 3 bots · 3 repeticiones")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Conversaciones" })).toHaveAttribute("aria-selected", "true");

    fireEvent.click(screen.getByRole("tab", { name: "Banco y corridas" }));
    expect(screen.getByRole("tab", { name: "Banco y corridas" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("table", { name: "Corridas" })).toBeInTheDocument();
  });

  it("la pestaña Resumen muestra el resumen de la corrida (PR 13)", async () => {
    runsResponse = { status: 200, body: { runs: [{ run_id: RUN, bench_id: "bench-x", arms: ["A0", "A1", "B"], reps: 3, phase: "done" }] } };
    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "Resumen" }));
    expect(screen.getByRole("tab", { name: "Resumen" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText(/resumen/i, { selector: "p" })).toBeInTheDocument();
  });
});
