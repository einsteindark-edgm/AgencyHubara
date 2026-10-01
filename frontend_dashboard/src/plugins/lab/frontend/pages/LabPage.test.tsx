import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
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

  it("Nueva corrida ofrece los bancos de todas las corridas, sin repetir y del más nuevo al más viejo", async () => {
    runsResponse = { status: 200, body: { runs: [
      { run_id: "caso-cortesia-1001-r4", bench_id: "caso-cortesia-1001", arms: ["A1", "B"], reps: 1, phase: "done" },
      { run_id: "caso-4148-real-r3", bench_id: "caso-4148-real", arms: ["A1", "B"], reps: 1, phase: "done" },
      { run_id: "caso-cortesia-1001-r3", bench_id: "caso-cortesia-1001", arms: ["A1", "B"], reps: 1, phase: "done" },
      { run_id: "caso-fotos-0930-r1", bench_id: "caso-fotos-0930", arms: ["A1", "B"], reps: 1, phase: "done" },
    ] } };
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Nueva corrida" }));
    fireEvent.click(screen.getByRole("button", { name: "Repetir un banco guardado" }));

    const picker = screen.getByRole("combobox", { name: "Banco" });
    expect(within(picker).getAllByRole("option").map((o) => o.textContent)).toEqual(["caso-cortesia-1001", "caso-4148-real", "caso-fotos-0930"]);
  });

  it("la sección ocupa todo el ancho de la ventana (sin franja negra a la derecha)", async () => {
    renderPage();

    // El contenedor del shell es `display:flex` en fila: sin crecer, la
    // sección mide lo que su contenido y deja el resto de la ventana vacío.
    expect(await screen.findByRole("region", { name: "Laboratorio" })).toHaveClass("flex-1", "min-w-0");
  });

  it("la pestaña Resumen muestra el resumen de la corrida (PR 13)", async () => {
    runsResponse = { status: 200, body: { runs: [{ run_id: RUN, bench_id: "bench-x", arms: ["A0", "A1", "B"], reps: 3, phase: "done" }] } };
    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "Resumen" }));
    expect(screen.getByRole("tab", { name: "Resumen" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText(/resumen/i, { selector: "p" })).toBeInTheDocument();
  });

  it("«Ver conversación» en el Resumen abre esa conversación en Conversaciones", async () => {
    const SID = "wa_573007654321";
    runsResponse = { status: 200, body: { runs: [{ run_id: RUN, bench_id: "bench-x", arms: ["A0", "A1", "B"], reps: 1, phase: "done" }] } };
    const base = fetchMock.getMockImplementation();
    fetchMock.mockImplementation((url: string) => {
      const u = String(url);
      if (u.endsWith(`/runs/${RUN}/report`)) return json({ run_id: RUN, mode: "turn", arms: ["A0", "A1", "B"], arena: {}, diffs: ["A1:B"] });
      if (u.includes(`/runs/${RUN}/diff?base=A1&cand=B`)) {
        return json({ base: "A1", cand: "B", episode_pass: { delta: 0, low: 0, high: 0, conclusive: false, sessions: 2 },
          checks: [], changed_turns: [{ session_id: SID, episode_id: "ep_1", turn: 3, base: "PASA", cand: "ALERTA", checks: [] }] });
      }
      if (u.endsWith(`/runs/${RUN}/conversations`)) {
        return json({ conversations: [
          { session_id: "wa_573001234567", turns: 2, episodes: ["ep_1"], verdicts: {} },
          { session_id: SID, turns: 4, episodes: ["ep_1"], verdicts: {} },
        ] });
      }
      return base!(url);
    });
    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Resumen" }));

    fireEvent.click(await screen.findByRole("button", { name: "Ver conversación" }));

    expect(screen.getByRole("tab", { name: "Conversaciones" })).toHaveAttribute("aria-selected", "true");
    const list = await screen.findByRole("list", { name: "Conversaciones del banco" });
    expect(await within(list).findByRole("button", { current: true })).toHaveTextContent("Cliente ···4321");
  });
});
