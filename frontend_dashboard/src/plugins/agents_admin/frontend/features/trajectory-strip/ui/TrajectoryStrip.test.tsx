import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import checksFixture from "@plugins/agents_admin/frontend/entities/scorecard/fixtures/checks.json";
import detailFixture from "@plugins/agents_admin/frontend/entities/scorecard/fixtures/scorecard-detail.json";
import {
  checkRegistrySchema,
  scorecardDetailSchema,
} from "@plugins/agents_admin/frontend/entities/scorecard/contracts";

import { TrajectoryStrip } from "./TrajectoryStrip";

const registry = checkRegistrySchema.parse(checksFixture);
const detail = scorecardDetailSchema.parse(detailFixture);

describe("TrajectoryStrip (scorecard → tira compartida)", () => {
  it("arma el modelo desde la trayectoria y los resultados y lo dibuja", () => {
    const onSelect = vi.fn();
    render(
      <TrajectoryStrip
        trajectory={detail.trajectory!}
        results={detail.scorecard!.results}
        registry={registry}
        selectedCheckId={null}
        onSelectCheck={onSelect}
      />,
    );
    const svg = screen.getByRole("group", { name: /tira de trayectoria/i });
    expect(within(svg).getByText("confirmación")).toBeInTheDocument();
    expect(within(svg).getByText(/^turno 10/)).toBeInTheDocument();
    expect(screen.getByText(/^primer crítico$/)).toBeInTheDocument();
    // Nombre del check (del registro) + turno ghost sin texto del bot.
    fireEvent.click(screen.getByRole("button", { name: /VAR-01.*falla crítica/i }));
    expect(onSelect).toHaveBeenCalledWith("VAR-01");
    expect(within(svg).getByText(/turno 8 · ghosting/)).toBeInTheDocument();
  });

  it("explica cuando no hay turnos", () => {
    render(
      <TrajectoryStrip
        trajectory={{ ...detail.trajectory!, turns: [] }}
        results={[]}
        selectedCheckId={null}
        onSelectCheck={() => {}}
      />,
    );
    expect(screen.getByText(/sin turnos registrados/i)).toBeInTheDocument();
  });
});
