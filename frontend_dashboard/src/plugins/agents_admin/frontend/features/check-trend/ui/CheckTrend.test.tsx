import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import statsFixture from "@plugins/agents_admin/frontend/entities/check-stats/fixtures/check-stats.json";
import { checkStatsSchema } from "@plugins/agents_admin/frontend/entities/check-stats/contracts";

import { CheckTrend } from "./CheckTrend";

const stats = checkStatsSchema.parse(statsFixture);

describe("CheckTrend", () => {
  it("por defecto muestra solo los checks que fallaron en la ventana", () => {
    render(<CheckTrend trend={stats.trend} />);
    for (const id of ["CON-01", "VAR-01", "DES-01", "TAG-01"]) {
      expect(screen.getByRole("img", { name: new RegExp(`^${id}`) })).toBeInTheDocument();
    }
    expect(screen.queryByRole("img", { name: /^APE-01/ })).toBeNull();
  });

  it("el toggle 'todos' muestra también los que siempre pasaron", () => {
    render(<CheckTrend trend={stats.trend} />);
    const toggle = screen.getByRole("button", { name: /todos los checks/i });
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("img", { name: /^APE-01/ })).toBeInTheDocument();
  });

  it("muestra último valor y delta contra la semana anterior", () => {
    render(<CheckTrend trend={stats.trend} />);
    const card = screen.getByRole("img", { name: /^CON-01/ }).closest("article")!;
    expect(card).toHaveTextContent("100 %");
    expect(card).toHaveTextContent("▲ 38 pts vs semana anterior");
    expect(card).toHaveTextContent("27 jul");
    expect(card).toHaveTextContent("14 sep");
  });

  it("sin checks con fallas lo dice", () => {
    render(<CheckTrend trend={stats.trend.filter((t) => t.check_id === "APE-01")} />);
    expect(screen.getByText(/ningún check falló en la ventana/i)).toBeInTheDocument();
  });
});
