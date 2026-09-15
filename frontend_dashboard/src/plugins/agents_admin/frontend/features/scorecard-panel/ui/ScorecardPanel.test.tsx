import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";

import checksFixture from "@plugins/agents_admin/frontend/entities/scorecard/fixtures/checks.json";
import detailFixture from "@plugins/agents_admin/frontend/entities/scorecard/fixtures/scorecard-detail.json";
import labelsFixture from "@plugins/agents_admin/frontend/entities/eval-label/fixtures/labels.json";
import createdFixture from "@plugins/agents_admin/frontend/entities/eval-label/fixtures/label-created.json";
import {
  checkRegistrySchema,
  scorecardDetailSchema,
} from "@plugins/agents_admin/frontend/entities/scorecard/contracts";

import { ScorecardPanel } from "./ScorecardPanel";

const registry = checkRegistrySchema.parse(checksFixture);
const detail = scorecardDetailSchema.parse(detailFixture);
const EPISODE = { sessionId: "wa_570000000001", episodeId: "ep_007" };
const fetchMock = vi.fn();

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === "POST" && url.includes("/scorecard/rescore")) return Promise.resolve(json(detailFixture));
    if (init?.method === "POST" && url.includes("/labels")) return Promise.resolve(json(createdFixture));
    if (url.includes("/api/agents/evals/labels?")) return Promise.resolve(json(labelsFixture));
    return Promise.resolve(json({}));
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function postBodies(path: string) {
  return fetchMock.mock.calls
    .filter(([url, init]) => String(url).includes(path) && init?.method === "POST")
    .map(([, init]) => JSON.parse(init.body));
}

describe("ScorecardPanel", () => {
  it("encabeza con el veredicto, los primeros fallos, conteos y fidelidad", () => {
    renderWithClient(
      <ScorecardPanel episode={EPISODE} detail={detail} registry={registry} selectedCheckId={null} onSelectCheck={() => {}} />,
    );
    expect(screen.getByText("FALLA")).toBeInTheDocument();
    expect(screen.getByText(/primer fallo · turno 2 · DES-09/)).toBeInTheDocument();
    expect(screen.getByText("5 críticos")).toBeInTheDocument();
    expect(screen.getByText("5 mayores")).toBeInTheDocument();
    expect(screen.getByText("3 menores")).toBeInTheDocument();
    expect(screen.getByText("24 pasan")).toBeInTheDocument();
    expect(screen.getByText("18 no aplican")).toBeInTheDocument();
    expect(screen.getByText("1 desconocido")).toBeInTheDocument();
    expect(screen.getByText(/cumplimiento 65 %/)).toBeInTheDocument();
    expect(screen.getByText(/puntaje legado 0\.93/)).toHaveClass("line-through");
    expect(screen.getByText("traza completa")).toBeInTheDocument();
    expect(screen.getByText(/elige un check/i)).toBeInTheDocument();
  });

  it("lista los checks por familia y seleccionar uno avisa al padre", () => {
    const onSelect = vi.fn();
    renderWithClient(
      <ScorecardPanel episode={EPISODE} detail={detail} registry={registry} selectedCheckId={null} onSelectCheck={onSelect} />,
    );
    expect(screen.getByRole("heading", { name: /Etiquetado y escalación/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /CON-01 Formulario de envío solo tras confirmación/ }));
    expect(onSelect).toHaveBeenCalledWith("CON-01");
    // Checks del juez sin resultado (juez apagado) se ven como sin evaluar.
    expect(screen.getByRole("button", { name: /DES-07 .*sin evaluar/ })).toBeInTheDocument();
  });

  it("muestra la regla, la evidencia y las etiquetas humanas del check seleccionado", async () => {
    renderWithClient(
      <ScorecardPanel episode={EPISODE} detail={detail} registry={registry} selectedCheckId="CON-01" onSelectCheck={() => {}} />,
    );
    const rule = registry.checks.find((c) => c.id === "CON-01")!.rule;
    expect(screen.getByText(rule)).toBeInTheDocument();
    expect(screen.getByText(/formulario de envío sin confirmación de compra/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/El cliente aplazó; el formulario no debía salir/)).toBeInTheDocument());
  });

  it("etiqueta el check seleccionado con nota", async () => {
    renderWithClient(
      <ScorecardPanel episode={EPISODE} detail={detail} registry={registry} selectedCheckId="CON-01" onSelectCheck={() => {}} />,
    );
    fireEvent.change(screen.getByLabelText(/nota de la etiqueta/i), { target: { value: "confirmado a mano" } });
    fireEvent.click(screen.getByRole("button", { name: /marcar pasa/i }));
    await waitFor(() => expect(postBodies("/api/agents/evals/labels")).toHaveLength(1));
    expect(postBodies("/api/agents/evals/labels")[0]).toEqual({
      session_id: "wa_570000000001",
      episode_id: "ep_007",
      check_id: "CON-01",
      verdict: "pasa",
      note: "confirmado a mano",
    });
    await waitFor(() => expect(screen.getByText(/etiqueta guardada/i)).toBeInTheDocument());
  });

  it("recalcula con juez cuando el scorecard anterior lo usó", async () => {
    renderWithClient(
      <ScorecardPanel episode={EPISODE} detail={detail} registry={registry} selectedCheckId={null} onSelectCheck={() => {}} />,
    );
    // El scorecard guardado ya usó juez: el toggle arranca activado.
    expect(screen.getByRole("checkbox", { name: /con juez/i })).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: /recalcular/i }));
    await waitFor(() => expect(postBodies("/scorecard/rescore")).toEqual([
      { session_id: "wa_570000000001", episode_id: "ep_007", judge: true },
    ]));
  });

  it("sin scorecard ofrece recalcular", () => {
    renderWithClient(
      <ScorecardPanel
        episode={EPISODE}
        detail={{ stored: false, scorecard: null, trajectory: null, legacy: null }}
        registry={registry}
        selectedCheckId={null}
        onSelectCheck={() => {}}
      />,
    );
    expect(screen.getByText(/aún no tiene scorecard/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /recalcular/i })).toBeInTheDocument();
  });
});
