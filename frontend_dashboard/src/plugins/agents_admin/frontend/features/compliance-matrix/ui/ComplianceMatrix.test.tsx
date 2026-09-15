import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import checksFixture from "@plugins/agents_admin/frontend/entities/scorecard/fixtures/checks.json";
import listFixture from "@plugins/agents_admin/frontend/entities/scorecard/fixtures/scorecards.json";

import type { VerdictFilter } from "../lib/matrix";
import { ComplianceMatrix } from "./ComplianceMatrix";

const fetchMock = vi.fn();
let listPayload: unknown = listFixture;

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  listPayload = listFixture;
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((url: string) => {
    if (url.includes("/api/agents/evals/checks")) return Promise.resolve(json(checksFixture));
    if (url.includes("/api/agents/evals/scorecards")) return Promise.resolve(json(listPayload));
    return Promise.resolve(json({}));
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function Harness({
  onSelectEpisode = () => {},
  checkFilter = null,
  onClearCheckFilter = () => {},
  rowCap,
}: {
  onSelectEpisode?: (s: string, e: string) => void;
  checkFilter?: string | null;
  onClearCheckFilter?: () => void;
  rowCap?: number;
}) {
  const [verdict, setVerdict] = useState<VerdictFilter>("todos");
  return (
    <ComplianceMatrix
      days={30}
      verdictFilter={verdict}
      onVerdictFilterChange={setVerdict}
      checkFilter={checkFilter}
      onClearCheckFilter={onClearCheckFilter}
      selectedEpisode={null}
      onSelectEpisode={onSelectEpisode}
      rowCap={rowCap}
    />
  );
}

function renderMatrix(props: Parameters<typeof Harness>[0] = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Harness {...props} />
    </QueryClientProvider>,
  );
}

async function bodyRows() {
  const table = await screen.findByRole("table", { name: /matriz de cumplimiento/i });
  return within(table).getAllByRole("row").filter((r) => r.closest("tbody"));
}

describe("ComplianceMatrix", () => {
  it("una fila por episodio con veredicto y celdas con glifo", async () => {
    renderMatrix();
    expect(await bodyRows()).toHaveLength(7);
    const table = screen.getByRole("table", { name: /matriz de cumplimiento/i });
    expect(within(table).getByRole("columnheader", { name: "confirmación" })).toBeInTheDocument();
    expect(within(table).getAllByTitle("VAR-01 · falla crítica").length).toBeGreaterThan(0);
  });

  it("filtra por veredicto", async () => {
    renderMatrix();
    await bodyRows();
    fireEvent.click(screen.getByRole("button", { name: "Alerta" }));
    await waitFor(async () => expect(await bodyRows()).toHaveLength(2));
    expect(screen.getByRole("button", { name: "Alerta" })).toHaveAttribute("aria-pressed", "true");
  });

  it("filtra por etapa final y por checks con fallas", async () => {
    renderMatrix();
    await bodyRows();
    fireEvent.change(screen.getByLabelText(/etapa final/i), { target: { value: "cierre" } });
    await waitFor(async () => expect(await bodyRows()).toHaveLength(2));
    fireEvent.click(screen.getByRole("checkbox", { name: /solo checks con fallas/i }));
    const table = screen.getByRole("table", { name: /matriz de cumplimiento/i });
    expect(within(table).queryByTitle(/^APE-01 ·/)).toBeNull();
    expect(within(table).getAllByTitle(/^CIE-03 ·/).length).toBeGreaterThan(0);
  });

  it("filtra por el check elegido en el Pareto y permite quitar el filtro", async () => {
    const onClear = vi.fn();
    renderMatrix({ checkFilter: "VAR-07", onClearCheckFilter: onClear });
    expect(await bodyRows()).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: /quitar filtro VAR-07/i }));
    expect(onClear).toHaveBeenCalled();
  });

  it("seleccionar una fila (click o Enter) abre el episodio", async () => {
    const onSelect = vi.fn();
    renderMatrix({ onSelectEpisode: onSelect });
    const rows = await bodyRows();
    fireEvent.click(rows[2]);
    expect(onSelect).toHaveBeenCalledWith("wa_100000000003", "ep_003");
    fireEvent.keyDown(rows[0], { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("wa_100000000001", "ep_007");
  });

  it("con muchos episodios pinta un tope de filas y deja pedir más", async () => {
    const base = (listFixture as { scorecards: Record<string, unknown>[] }).scorecards;
    const many = Array.from({ length: 8 }, (_, i) => ({
      ...base[i % base.length],
      session_id: `wa_2${String(i).padStart(9, "0")}`,
      episode_id: "ep_001",
      episode_date: "2026-09-10",
    }));
    listPayload = { days: 30, count: many.length, registry_version: 1, scorecards: many };
    renderMatrix({ rowCap: 5 });
    expect(await bodyRows()).toHaveLength(5);
    expect(screen.getByText(/8 de 8 episodios/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /mostrar 3 más/i }));
    await waitFor(async () => expect(await bodyRows()).toHaveLength(8));
    expect(screen.queryByRole("button", { name: /mostrar .* más/i })).toBeNull();
    // La fecha visible es la del episodio, no la de la evaluación.
    expect(screen.getAllByText(/2026-09-10/).length).toBeGreaterThan(0);
  });

  it("sin scorecards explica cuándo se generan", async () => {
    listPayload = { scorecards: [] };
    renderMatrix();
    expect(await screen.findByText(/aún no hay scorecards: se generan al cerrar cada episodio/i)).toBeInTheDocument();
  });
});
