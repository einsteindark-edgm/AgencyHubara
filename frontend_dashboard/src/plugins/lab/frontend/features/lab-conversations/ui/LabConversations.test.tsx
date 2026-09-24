import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import threadFixture from "@plugins/lab/frontend/entities/lab-run/fixtures/thread.json";
import type { LabRun } from "@plugins/lab/frontend/entities/lab-run";

import { LabConversations } from "./LabConversations";

/**
 * Pestaña "Conversaciones" (diseño §09): la lista del banco con el veredicto
 * de cada bot; el hilo con un selector de bot; a la derecha el resumen de
 * evaluaciones y la subpestaña Evaluaciones; cada ráfaga y cada turno abren
 * el modal del hilo del turno.
 */

const RUN = "run-20260923-1041-ab12";
const SID = "wa_573001234567";
const OTHER = "wa_573007654321";
const run: LabRun = {
  run_id: RUN, bench_id: "bench-x", arms: ["A0", "A1", "B"], reps: 1, registry_version: 3, counts: {}, phase: "done",
  turns_done: null, turns_total: null, spent_usd: null, error: null, notes: [], started_at_ms: null, updated_at_ms: null,
};

const fetchMock = vi.fn();

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((url: string) => {
    const u = String(url);
    if (u.endsWith(`/runs/${RUN}/conversations`)) {
      return json({
        conversations: [
          { session_id: SID, turns: 2, episodes: ["ep_1"], last_at_ms: 1790178069000, verdicts: { A0: { ep_1: "ALERTA" } } },
          { session_id: OTHER, turns: 5, episodes: ["ep_1", "ep_2"], last_at_ms: 1790090000000, verdicts: { A0: { ep_1: "PASA", ep_2: "FALLA" } } },
        ],
      });
    }
    if (u.includes(`/conversations/${SID}/turns/trace`)) {
      return json({ fidelity: "v1", arm: "A0", rep: 0, trace: {}, steps: [{ i: 1, at_ms: null, kind: "inbound", messages: [{ text: "hola" }] }] });
    }
    if (u.includes(`/conversations/${SID}/evaluations`)) {
      return json({
        arm: "A0", rep: 0,
        episodes: [{ session_id: SID, episode_id: "ep_1", verdict: "ALERTA", results: [
          { check_id: "EST-06", verdict: "falla", turn: 2, evidence: "El texto escrito junto a send_shipping_rates se descartó." },
          { check_id: "APE-01", verdict: "pasa", turn: 1, evidence: "Saludó según la hora." },
          { check_id: "CON-05", verdict: "no_aplica", turn: null },
        ] }],
      });
    }
    if (u.includes(`/conversations/${SID}`)) return json(threadFixture);
    return json({ detail: "no" }, 404);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <LabConversations run={run} />
    </QueryClientProvider>,
  );
}

describe("LabConversations", () => {
  it("lista las conversaciones del banco con el peor veredicto de cada bot, sin el teléfono", async () => {
    renderTab();
    const list = await screen.findByRole("list", { name: "Conversaciones del banco" });

    const items = within(list).getAllByRole("button");
    expect(items[0]).toHaveTextContent("Cliente ···4567");
    expect(items[0]).toHaveTextContent("Producción ALERTA");
    expect(items[1]).toHaveTextContent("Producción FALLA");
    expect(items[0]).toHaveAttribute("aria-current", "true");
    expect(document.body.textContent).not.toContain("573001234567");
  });

  it("muestra el hilo de producción con la ráfaga agrupada y el botón de cada turno", async () => {
    renderTab();

    expect(await screen.findByText("Buenas tardes")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ver el hilo de la ráfaga de 2 mensajes" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^Ver hilo del turno/ })).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Producción" })).toHaveAttribute("aria-pressed", "true");
  });

  it("cambiar de bot muestra lo que ese bot respondió (o que todavía no corrió)", async () => {
    renderTab();
    await screen.findByText("Buenas tardes");
    fireEvent.click(screen.getByRole("button", { name: "Nuevo + Jev" }));

    expect(screen.getAllByText("Este bot todavía no respondió este turno.")).toHaveLength(2);
  });

  it("el botón del turno abre el modal del hilo de ese turno", async () => {
    renderTab();
    await screen.findByText("Buenas tardes");
    fireEvent.click(screen.getAllByRole("button", { name: /^Ver hilo del turno/ })[1]);

    expect(await screen.findByRole("dialog", { name: "Hilo del turno 2" })).toBeInTheDocument();
  });

  it("el resumen dice el veredicto de cada bot; los que no corrieron, pendiente", async () => {
    renderTab();
    const side = await screen.findByRole("tabpanel", { name: "Resumen" });

    expect(within(side).getByText("Producción").closest("div")).toHaveTextContent("ALERTA");
    expect(within(side).getByText("Nuevo + Jev").closest("div")).toHaveTextContent("pendiente");
  });

  it("la subpestaña Evaluaciones lista cada check con su veredicto y su evidencia; las fallas primero", async () => {
    renderTab();
    await screen.findByText("Buenas tardes");
    fireEvent.click(screen.getByRole("tab", { name: "Evaluaciones" }));

    const panel = await screen.findByRole("tabpanel", { name: "Evaluaciones" });
    const checks = await within(panel).findAllByRole("article");
    expect(checks[0]).toHaveTextContent("EST-06");
    expect(checks[0]).toHaveTextContent("falla");
    expect(checks[0]).toHaveTextContent("turno 2");
    expect(checks[0]).toHaveTextContent("se descartó");
    expect(checks[1]).toHaveTextContent("APE-01");
    expect(within(panel).queryByText("CON-05")).toBeNull();
  });

  it("en modo turno un check aparece por turno y los sin señal se cuentan", async () => {
    const base = fetchMock.getMockImplementation();
    fetchMock.mockImplementation((url: string) =>
      String(url).includes(`/conversations/${SID}/evaluations`)
        ? json({
            arm: "B", rep: 0,
            episodes: [{ session_id: SID, episode_id: "ep_1", verdict: "PASA", results: [
              { check_id: "EST-06", verdict: "pasa", turn: 1 },
              { check_id: "EST-06", verdict: "pasa", turn: 2 },
              { check_id: "CIE-03", verdict: "sin_senal", turn: 2 },
            ] }],
          })
        : base!(url),
    );
    renderTab();
    await screen.findByText("Buenas tardes");
    fireEvent.click(screen.getByRole("tab", { name: "Evaluaciones" }));

    const panel = await screen.findByRole("tabpanel", { name: "Evaluaciones" });
    const checks = await within(panel).findAllByRole("article");
    expect(checks.map((c) => c.textContent)).toEqual([
      expect.stringContaining("turno 1"),
      expect.stringContaining("turno 2"),
    ]);
    expect(within(panel).getByText(/1 check sin señal/)).toBeInTheDocument();
  });

  it("elegir otra conversación la marca", async () => {
    renderTab();
    const list = await screen.findByRole("list", { name: "Conversaciones del banco" });
    fireEvent.click(within(list).getAllByRole("button")[1]);

    expect(within(list).getAllByRole("button")[1]).toHaveAttribute("aria-current", "true");
  });
});
