import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";

import statsFixture from "@plugins/agents_admin/frontend/entities/check-stats/fixtures/check-stats.json";
import { checkStatsSchema } from "@plugins/agents_admin/frontend/entities/check-stats/contracts";

import { StageFunnel } from "./StageFunnel";

const stats = checkStatsSchema.parse(statsFixture);

describe("StageFunnel (scorecard → gráfica compartida)", () => {
  it("ordena las etapas del contrato por el guion y las rotula", () => {
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
  });
});
