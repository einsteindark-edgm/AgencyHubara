/**
 * Rediseño del modal "Analizar con IA" (feedback operador 2026-07-09):
 * - el JSON de ejemplo confundía → con Meta conectado se envían los DATOS
 *   REALES (analysis-input) y el JSON queda colapsado en un <details>;
 * - el selector de agente dice QUÉ análisis hace (description del catálogo);
 * - Run manda el input real, no el ejemplo.
 */

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";

const mockAgents = vi.hoisted(() => ({ current: [] as object[] }));
const mockMutate = vi.hoisted(() => vi.fn());
const mockConn = vi.hoisted(() => ({ current: {} as object }));
const mockLive = vi.hoisted(() => ({ current: {} as object }));

vi.mock("@plugins/ads/frontend/entities/ad-analysis-run", () => ({
  useAgents: () => ({ data: mockAgents.current, isLoading: false, isError: false }),
  useTriggerRun: () => ({ mutate: mockMutate, isPending: false, isError: false }),
}));

vi.mock("@plugins/ads/frontend/entities/meta-connection", () => ({
  useMetaConnection: () => mockConn.current,
  useMetaAnalysisInput: () => mockLive.current,
}));

import { TriggerRun } from "./TriggerRun";

const AGENT = {
  id: "ads-analytics",
  label: "Unit-economics CTWA por campaña",
  description: "Cruza el gasto de Meta con las ventas y arma el embudo por campaña.",
  exampleInput: { seed: "ejemplo" },
};

const LIVE = { meta_insights: { data: [{ spend: "120000" }] } };

function setup(opts: { connected: boolean; live?: unknown }) {
  mockAgents.current = [AGENT];
  mockConn.current = {
    data: opts.connected
      ? { connected: true, expired: false, accountName: "Hubara" }
      : { connected: false },
  };
  mockLive.current = { data: opts.live, isLoading: false };
  mockMutate.mockClear();
  return render(<TriggerRun onRunStarted={() => {}} />);
}

describe("TriggerRun — selector con descripción", () => {
  it("muestra qué análisis hace el agente elegido", () => {
    const { getByText } = setup({ connected: false });
    expect(getByText(/cruza el gasto de meta con las ventas/i)).toBeTruthy();
  });
});

describe("TriggerRun — datos reales por default", () => {
  it("con Meta conectado: etiqueta los datos como reales y el JSON va colapsado", () => {
    const { getByText, container } = setup({ connected: true, live: LIVE });
    expect(getByText(/datos reales de meta/i)).toBeTruthy();
    const details = container.querySelector("details");
    expect(details).toBeTruthy(); // el JSON vive en un desplegable…
    expect(details!.open).toBe(false); // …cerrado por default (no asusta)
  });

  it("Run dispara con los datos REALES, no con el ejemplo", () => {
    const { getByRole } = setup({ connected: true, live: LIVE });
    fireEvent.click(getByRole("button", { name: /analizar/i }));
    expect(mockMutate).toHaveBeenCalledTimes(1);
    expect(mockMutate.mock.calls[0][0]).toEqual({
      agent: "ads-analytics",
      input: LIVE,
    });
  });

  it("sin Meta conectado: avisa que va el ejemplo (fallback honesto)", () => {
    const { getByText } = setup({ connected: false });
    expect(getByText(/se enviará un JSON de ejemplo/i)).toBeTruthy();
  });
});
