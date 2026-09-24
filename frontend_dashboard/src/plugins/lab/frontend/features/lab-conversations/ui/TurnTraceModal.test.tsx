import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import threadFixture from "@plugins/lab/frontend/entities/lab-run/fixtures/thread.json";
import { threadSchema } from "@plugins/lab/frontend/entities/lab-run";

import { TurnTraceModal } from "./TurnTraceModal";

/**
 * Modal del hilo de un turno (plan §11.1): encabezado con el turno, la ráfaga
 * y el bot; asuntos y checks del turno; el diagrama de secuencia a la
 * izquierda y el detalle del paso a la derecha; abajo, el resultado.
 */

const RUN = "run-20260923-1041-ab12";
const thread = threadSchema.parse(threadFixture);
const turn = thread.turns[1];

const traceA0 = {
  fidelity: "v2",
  arm: "A0",
  rep: 0,
  trace: {},
  steps: [
    { i: 0, at_ms: 0, kind: "inbound", messages: turn.burst },
    { i: 1, at_ms: 10, dur_ms: 1900, kind: "llm", round: 1, finish: "tool_calls", tool_calls: ["send_shipping_rates"], text_fate: "discarded_default_deny", text: "¡Claro! te comparto el catálogo" },
    { i: 2, at_ms: 1950, dur_ms: 300, kind: "tool", name: "send_shipping_rates", ok: true },
    { i: 3, at_ms: 2300, kind: "cut", reason: "awaits_customer" },
  ],
};

const evaluations = {
  arm: "A0",
  rep: 0,
  episodes: [
    {
      session_id: thread.session_id,
      episode_id: "ep_1",
      verdict: "ALERTA",
      results: [
        { check_id: "EST-06", verdict: "falla", turn: 2, evidence: "texto descartado" },
        { check_id: "ENV-02", verdict: "pasa", turn: null },
        { check_id: "EST-08", verdict: "falla", turn: 2, topics: [
          { topic: "catálogo", turn: 2, msg: 1, covered: false, evidence: "" },
          { topic: "envío a Bogotá", turn: 2, msg: 2, covered: true, evidence: "" },
        ] },
        { check_id: "APE-01", verdict: "pasa", turn: 1 },
      ],
    },
  ],
};

const fetchMock = vi.fn();

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((url: string) => {
    const u = String(url);
    if (u.includes("/turns/trace")) {
      return u.includes("arm=B") ? json({ detail: "Ese turno no tiene traza en este brazo." }, 404) : json(traceA0);
    }
    if (u.includes("/evaluations")) return json(evaluations);
    return json({}, 404);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function renderModal(onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TurnTraceModal run={RUN} sid={thread.session_id} turn={turn} arms={["A0", "B"]} initialArm="A0" onClose={onClose} />
    </QueryClientProvider>,
  );
  return onClose;
}

describe("TurnTraceModal", () => {
  it("abre como diálogo con el turno, la ráfaga y el bot", async () => {
    renderModal();

    const dialog = screen.getByRole("dialog", { name: "Hilo del turno 2" });
    expect(within(dialog).getByText(/ráfaga de 2 mensajes/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Producción" })).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(within(dialog).getAllByRole("button", { name: /^Paso \d+:/ }).length).toBeGreaterThan(0));
  });

  it("muestra los asuntos y los checks de ESTE turno", async () => {
    renderModal();

    expect(await screen.findByText("catálogo · mensaje 1 · sin responder")).toBeInTheDocument();
    expect(screen.getByText("envío a Bogotá · mensaje 2 · respondido")).toBeInTheDocument();
    expect(screen.getByText("EST-06 · falla")).toBeInTheDocument();
    expect(screen.queryByText("APE-01 · pasa")).toBeNull();
  });

  it("el primer paso sale seleccionado y clic en otro cambia el detalle", async () => {
    renderModal();
    const seq = await screen.findByRole("group", { name: "Secuencia de pasos del turno" });

    expect(screen.getByRole("heading", { level: 4, name: /^1\. Ráfaga · 2 mensajes/ })).toBeInTheDocument();
    fireEvent.click(within(seq).getByRole("button", { name: "Paso 3: Pide send_shipping_rates" }));
    expect(screen.getByRole("heading", { level: 4, name: "3. Pide send_shipping_rates" })).toBeInTheDocument();
    expect(screen.getByText("Descartado: venía junto a una tool (default-deny)")).toBeInTheDocument();
  });

  it("el resultado resume los checks que fallan en el turno", async () => {
    renderModal();

    expect(await screen.findByText("2 checks fallan en este turno: EST-06, EST-08.")).toBeInTheDocument();
  });

  it("cambiar de bot pide la traza de ese bot y dice si no la hay", async () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: "Nuevo + Jev" }));

    expect(await screen.findByText("Ese turno no tiene traza en este brazo.")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("arm=B"))).toBe(true);
  });

  it("el botón de cerrar y Escape cierran", async () => {
    const onClose = renderModal();

    fireEvent.click(screen.getByRole("button", { name: "Cerrar el hilo" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
