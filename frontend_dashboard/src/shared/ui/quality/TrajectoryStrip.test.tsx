import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import stripModelFixture from "@/shared/lib/fixtures/strip-model.json";
import type { StripModel } from "@/shared/lib";

import { TrajectoryStrip } from "./TrajectoryStrip";

/** Modelo de vista de un episodio real de 10 turnos (el que arma la entity scorecard de agents_admin). */
const model = stripModelFixture as StripModel;

function renderStrip(selectedCheckId: string | null = null, onSelectCheck = vi.fn()) {
  render(
    <TrajectoryStrip
      model={model}
      selectedCheckId={selectedCheckId}
      onSelectCheck={onSelectCheck}
    />,
  );
  return onSelectCheck;
}

describe("TrajectoryStrip", () => {
  it("dibuja la tira con etapas rotuladas, carriles y turnos", () => {
    renderStrip();
    const svg = screen.getByRole("group", { name: /tira de trayectoria/i });
    // Bandas con texto visible (nunca solo color).
    expect(within(svg).getByText("descubrimiento")).toBeInTheDocument();
    expect(within(svg).getByText("variantes")).toBeInTheDocument();
    expect(within(svg).getByText("confirmación")).toBeInTheDocument();
    for (const lane of ["cliente", "bot", "tools", "componentes", "estado", "guardas", "checks"]) {
      expect(within(svg).getByText(lane)).toBeInTheDocument();
    }
    expect(within(svg).getByText(/^turno 10/)).toBeInTheDocument();
  });

  it("marca el primer fallo y el primer crítico", () => {
    renderStrip();
    expect(screen.getByText(/^primer fallo$/)).toBeInTheDocument();
    expect(screen.getByText(/^primer crítico$/)).toBeInTheDocument();
  });

  it("muestra la narración descartada como chip con su motivo en el tooltip", () => {
    const { container } = render(
      <TrajectoryStrip model={model} selectedCheckId={null} onSelectCheck={() => {}} />,
    );
    const titles = [...container.querySelectorAll("title")].map((t) => t.textContent ?? "");
    expect(titles.some((t) => /descartada por default-deny/i.test(t) && /formulario/.test(t))).toBe(true);
    expect(titles.some((t) => /request_shipping_details\(order_total_cop=89000\)/.test(t))).toBe(true);
    expect(titles.some((t) => /Sin turno/i.test(t) && /EST-01/.test(t))).toBe(true);
  });

  it("seleccionar un check (click o teclado) avisa al padre", () => {
    const onSelect = renderStrip();
    fireEvent.click(screen.getByRole("button", { name: /VAR-01.*falla crítica/i }));
    expect(onSelect).toHaveBeenCalledWith("VAR-01");
    fireEvent.keyDown(screen.getByRole("button", { name: /CON-01.*falla crítica/i }), { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("CON-01");
  });

  it("resalta el check seleccionado", () => {
    renderStrip("TAG-01");
    expect(screen.getByRole("button", { name: /TAG-01 ·/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /VAR-01 ·/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("explica cuando no hay turnos", () => {
    render(
      <TrajectoryStrip
        model={{ columns: [], bands: [], firstFailure: null, firstCritical: null }}
        selectedCheckId={null}
        onSelectCheck={() => {}}
      />,
    );
    expect(screen.getByText(/sin turnos registrados/i)).toBeInTheDocument();
  });
});
