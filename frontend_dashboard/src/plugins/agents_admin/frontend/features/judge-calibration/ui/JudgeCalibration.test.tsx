import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import calibrationFixture from "@plugins/agents_admin/frontend/entities/eval-label/fixtures/calibration.json";
import createdFixture from "@plugins/agents_admin/frontend/entities/eval-label/fixtures/label-created.json";
import queueFixture from "@plugins/agents_admin/frontend/entities/eval-label/fixtures/labels-queue.json";

import { JudgeCalibration } from "./JudgeCalibration";

const fetchMock = vi.fn();
let queuePayload: unknown = queueFixture;

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  queuePayload = queueFixture;
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === "POST") return Promise.resolve(json(createdFixture));
    if (url.includes("/api/agents/evals/calibration")) return Promise.resolve(json(calibrationFixture));
    if (url.includes("/api/agents/evals/labels/queue")) return Promise.resolve(json(queuePayload));
    return Promise.resolve(json({}));
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
      <JudgeCalibration />
    </QueryClientProvider>,
  );
}

describe("JudgeCalibration", () => {
  it("tabla por check del juez con matriz, tasas, kappa y estado", async () => {
    renderIt();
    const table = await screen.findByRole("table", { name: /calibración del juez/i });
    const row = within(table).getByRole("row", { name: /DES-04/ });
    expect(row).toHaveTextContent("34");
    expect(row).toHaveTextContent("86 %");
    expect(row).toHaveTextContent("0.76");
    expect(row).toHaveTextContent("confiable");
    expect(within(table).getByRole("row", { name: /VAR-05/ })).toHaveTextContent("revisar");
    expect(within(table).getByRole("row", { name: /CON-04/ })).toHaveTextContent("sin datos");
    expect(screen.getByText(/al menos 20 etiquetas y κ ≥ 0.6/)).toBeInTheDocument();
  });

  it("etiqueta un item de la cola con nota", async () => {
    renderIt();
    const queue = await screen.findByRole("list", { name: /veredictos del juez por etiquetar/i });
    const item = within(queue).getByText("No elige variantes por el cliente").closest("li")!;
    expect(item).toHaveTextContent("el juez dijo falla");
    fireEvent.change(within(item).getByLabelText(/nota/i), { target: { value: "sí asumió el color" } });
    fireEvent.click(within(item).getByRole("button", { name: /^falla$/i }));
    await waitFor(() => {
      const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
      expect(posts).toHaveLength(1);
      expect(JSON.parse(posts[0][1].body)).toEqual({
        session_id: "wa_570000000005",
        episode_id: "ep_002",
        check_id: "VAR-05",
        verdict: "falla",
        note: "sí asumió el color",
      });
    });
  });

  it("cola vacía lo dice", async () => {
    queuePayload = { items: [] };
    renderIt();
    expect(await screen.findByText(/no hay veredictos del juez pendientes de etiquetar/i)).toBeInTheDocument();
  });
});
