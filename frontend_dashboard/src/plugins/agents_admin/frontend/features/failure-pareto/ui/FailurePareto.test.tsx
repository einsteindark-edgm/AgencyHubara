import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import statsFixture from "@plugins/agents_admin/frontend/entities/check-stats/fixtures/check-stats.json";
import { checkStatsSchema } from "@plugins/agents_admin/frontend/entities/check-stats/contracts";

import { FailurePareto } from "./FailurePareto";

const stats = checkStatsSchema.parse(statsFixture);

describe("FailurePareto", () => {
  it("una barra por check con fallos, nivel en texto y acumulado", () => {
    render(<FailurePareto pareto={stats.pareto} days={56} onSelectCheck={() => {}} />);
    expect(screen.getByRole("group", { name: /pareto de fallos/i })).toBeInTheDocument();
    const bar = screen.getByRole("button", { name: /DES-01: 14 fallos · mayor · 18 % acumulado/i });
    expect(bar).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /fallos ·/ })).toHaveLength(10);
    expect(screen.getByText("crítico")).toBeInTheDocument();
  });

  it("click o Enter en una barra elige el check", () => {
    const onSelect = vi.fn();
    render(<FailurePareto pareto={stats.pareto} days={56} onSelectCheck={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: /^CON-01:/ }));
    expect(onSelect).toHaveBeenCalledWith("CON-01");
    fireEvent.keyDown(screen.getByRole("button", { name: /^VAR-01:/ }), { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("VAR-01");
  });

  it("sin fallos lo dice", () => {
    render(<FailurePareto pareto={[]} days={56} onSelectCheck={() => {}} />);
    expect(screen.getByText(/ningún check falló en los últimos 56 días/i)).toBeInTheDocument();
  });
});
