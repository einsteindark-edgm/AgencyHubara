import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import statsFixture from "@/shared/lib/fixtures/quality-stats.json";
import type { VerdictCounts } from "@/shared/lib";

import { VerdictTiles } from "./VerdictTiles";

const stats = statsFixture as { episodes: number; verdicts: VerdictCounts };

describe("VerdictTiles", () => {
  it("un tile por veredicto con conteo y participación", () => {
    render(<VerdictTiles totals={stats.verdicts} episodes={stats.episodes} onSelectVerdict={() => {}} />);
    const tiles = screen.getByRole("list", { name: /veredictos de los episodios/i });
    expect(within(tiles).getAllByRole("listitem")).toHaveLength(4);
    const falla = within(tiles).getByRole("button", { name: /falla: 9 episodios — ver en Conversaciones/i });
    expect(falla).toHaveTextContent(`${Math.round((100 * 9) / stats.episodes)} % de ${stats.episodes}`);
    // "Sin datos" informa pero no es accionable.
    expect(within(tiles).queryByRole("button", { name: /sin datos/i })).toBeNull();
    expect(within(tiles).getByTitle(/sin trayectoria evaluable/i)).toHaveTextContent("Sin datos");
  });

  it("elegir un tile avisa el veredicto", () => {
    const onSelect = vi.fn();
    render(<VerdictTiles totals={stats.verdicts} episodes={stats.episodes} onSelectVerdict={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: /^alerta:/i }));
    expect(onSelect).toHaveBeenCalledWith("ALERTA");
  });

  it("la pista del aria-label es configurable y sin episodios la participación es 0 %", () => {
    render(
      <VerdictTiles
        totals={{ FALLA: 0, ALERTA: 0, PASA: 0, SIN_DATOS: 0 }}
        episodes={0}
        onSelectVerdict={() => {}}
        selectHint="ver en la corrida"
      />,
    );
    expect(screen.getByRole("button", { name: "Pasa: 0 episodios — ver en la corrida" })).toHaveTextContent("0 % de 0");
  });
});
