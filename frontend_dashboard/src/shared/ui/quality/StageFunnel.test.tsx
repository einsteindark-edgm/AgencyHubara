import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";

import statsFixture from "@/shared/lib/fixtures/quality-stats.json";
import type { FunnelRowView } from "@/shared/lib";

import { StageFunnel } from "./StageFunnel";

/** Embudo de vista ya ordenado por el guion y rotulado (lo arma `toFunnelView` en agents_admin). */
const stats = statsFixture as { funnel: FunnelRowView[] };

describe("StageFunnel", () => {
  it("una barra por etapa final con total, en el orden recibido (el del guion), y leyenda", () => {
    render(<StageFunnel funnel={stats.funnel} />);
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
