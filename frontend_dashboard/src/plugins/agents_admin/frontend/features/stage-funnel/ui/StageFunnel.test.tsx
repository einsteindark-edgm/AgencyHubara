import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";

import statsFixture from "@plugins/agents_admin/frontend/entities/check-stats/fixtures/check-stats.json";
import { checkStatsSchema } from "@plugins/agents_admin/frontend/entities/check-stats/contracts";

import { StageFunnel } from "./StageFunnel";

const stats = checkStatsSchema.parse(statsFixture);

describe("StageFunnel", () => {
  it("una barra por etapa final con total, en orden del guion, y leyenda", () => {
    render(<StageFunnel funnel={[...stats.funnel].reverse()} />);
    const chart = screen.getByRole("img", { name: /embudo de etapa terminal/i });
    const labels = within(chart).getAllByTestId("funnel-stage").map((n) => n.textContent);
    expect(labels).toEqual(["descubrimiento", "variantes", "confirmación", "datos de envío", "cierre"]);
    expect(within(chart).getAllByTestId("funnel-total").map((n) => n.textContent)).toEqual([
      "16",
      "9",
      "7",
      "3",
      "7",
    ]);
    const legend = screen.getByRole("list", { name: /leyenda/i });
    for (const v of ["Falla", "Alerta", "Pasa", "Sin datos"]) {
      expect(within(legend).getByText(v)).toBeInTheDocument();
    }
    expect(chart.querySelector("title")?.textContent).toMatch(/descubrimiento · Falla: 1/);
  });

  it("sin episodios lo dice", () => {
    render(<StageFunnel funnel={[]} />);
    expect(screen.getByText(/sin episodios en la ventana/i)).toBeInTheDocument();
  });
});
