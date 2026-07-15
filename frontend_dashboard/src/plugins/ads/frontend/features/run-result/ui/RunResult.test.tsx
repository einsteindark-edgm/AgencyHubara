/**
 * El resultado del análisis se muestra LEGIBLE (feedback operador 2026-07-10):
 * el pod proyecta {markdown, verdict, qa_passed} y la caja de resultado del
 * modal renderiza ese markdown FORMATEADO (títulos, tabla GFM del blend) — no
 * un JSON crudo. Los results legacy (sin markdown) caen al JSON de siempre.
 */
import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";

import type { RunRecord } from "@plugins/ads/frontend/entities/ad-analysis-run";

const mockRun = vi.hoisted(() => ({ current: null as unknown }));

vi.mock("@plugins/ads/frontend/entities/ad-analysis-run", async (importOriginal) => {
  const orig = await importOriginal<Record<string, unknown>>();
  return {
    ...orig,
    useRun: () => ({ data: mockRun.current, isLoading: false, isError: false }),
    useAdAnalysisRunEvents: () => {},
  };
});

import { RunResult } from "./RunResult";

function run(over: Partial<RunRecord>): RunRecord {
  return {
    runId: "run-1",
    agent: "ads-analytics",
    input: {},
    createdAtMs: null,
    campaignId: null,
    status: "completed",
    events: [],
    ...over,
  } as RunRecord;
}

const REPORT_MD = [
  "## Hubara — Ads Analytics (CTWA)",
  "",
  "| Fecha | Spend | MER |",
  "|---|--:|--:|",
  "| 2026-07-02 | 17.471 | 2.1 |",
  "",
  "**Diagnóstico del periodo:** escalar Día del padre.",
].join("\n");

describe("RunResult — reporte legible", () => {
  it("renderiza el markdown proyectado FORMATEADO (título + tabla GFM)", () => {
    mockRun.current = run({
      result: {
        markdown: REPORT_MD,
        verdict: "ok",
        qa_passed: true,
        _projected_from: "ctwa-report",
      },
    });
    const { getByRole, queryByText, getByText } = render(<RunResult runId="run-1" />);

    // el título se renderiza como heading, no como "## ..." crudo
    expect(getByRole("heading", { name: /Hubara — Ads Analytics/ })).toBeTruthy();
    expect(queryByText(/## Hubara/)).toBeNull();
    // la tabla GFM del blend es una TABLA de verdad
    expect(getByRole("table")).toBeTruthy();
    expect(getByRole("columnheader", { name: "Spend" })).toBeTruthy();
    expect(getByRole("cell", { name: "17.471" })).toBeTruthy();
    // y el verdict queda visible
    expect(getByText(/ok/)).toBeTruthy();
    // el JSON crudo del result NO se muestra como principal
    expect(queryByText(/"markdown"/)).toBeNull();
  });

  it("muestra la narrativa interpretativa del LLM cuando el result la trae", () => {
    mockRun.current = run({
      result: {
        markdown: REPORT_MD,
        verdict: "ok",
        qa_passed: true,
        narrative: "El MER del periodo fue 2.1 — conviene escalar Día del padre.",
        _projected_from: "ctwa-report",
      },
    });
    const { getByText } = render(<RunResult runId="run-1" />);
    expect(getByText(/conviene escalar Día del padre/)).toBeTruthy();
  });

  it("sin narrative (LLM degradado / record viejo) → el reporte sale igual, sin bloque narrativo", () => {
    mockRun.current = run({
      result: {
        markdown: REPORT_MD,
        verdict: "ok",
        qa_passed: true,
        _projected_from: "ctwa-report",
      },
    });
    const { getByRole, queryByText } = render(<RunResult runId="run-1" />);
    expect(getByRole("table")).toBeTruthy();
    expect(queryByText(/Lectura del análisis/)).toBeNull();
  });

  it("result legacy sin markdown → cae al JSON crudo de siempre", () => {
    mockRun.current = run({ result: { acc: { foo: 1 } } });
    const { getByText } = render(<RunResult runId="run-1" />);
    expect(getByText(/"acc"/)).toBeTruthy();
  });
});
