import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";

import matrixFixture from "@/shared/lib/fixtures/compliance-matrix.json";
import type { MatrixGroupView, MatrixRowView } from "@/shared/lib";

import { ComplianceMatrixLegend, ComplianceMatrixTable } from "./ComplianceMatrixTable";

/** Vista de 7 episodios × el registro de checks (la arma agents_admin desde sus scorecards). */
const { groups, rows } = matrixFixture as { groups: MatrixGroupView[]; rows: MatrixRowView[] };

function bodyRows() {
  const table = screen.getByRole("table", { name: /matriz de cumplimiento/i });
  return within(table).getAllByRole("row").filter((r) => r.closest("tbody"));
}

describe("ComplianceMatrixTable", () => {
  it("una fila por episodio con veredicto, rótulo, meta y celdas con glifo", () => {
    render(<ComplianceMatrixTable groups={groups} rows={rows} selectedKey={null} onSelectRow={() => {}} rowCap={120} resetKey="" />);
    expect(bodyRows()).toHaveLength(7);
    const table = screen.getByRole("table", { name: /matriz de cumplimiento/i });
    expect(within(table).getByRole("columnheader", { name: "confirmación" })).toBeInTheDocument();
    expect(within(table).getAllByTitle("VAR-01 · falla crítica").length).toBeGreaterThan(0);
    const first = bodyRows()[0];
    expect(first).toHaveTextContent(`FALLA${rows[0].label}${rows[0].meta}`);
    expect(within(first).getByTitle(rows[0].title)).toBeInTheDocument();
  });

  it("marca la fila seleccionada y avisa al elegir (click o Enter)", () => {
    const onSelect = vi.fn();
    render(
      <ComplianceMatrixTable groups={groups} rows={rows} selectedKey={rows[1].key} onSelectRow={onSelect} rowCap={120} resetKey="" />,
    );
    expect(bodyRows()[1]).toHaveAttribute("aria-current", "true");
    expect(bodyRows()[0]).not.toHaveAttribute("aria-current");
    fireEvent.click(bodyRows()[2]);
    expect(onSelect).toHaveBeenLastCalledWith(rows[2]);
    fireEvent.keyDown(bodyRows()[0], { key: "Enter" });
    expect(onSelect).toHaveBeenLastCalledWith(rows[0]);
  });

  it("pinta un tope de filas, deja pedir más y vuelve al tope al cambiar la clave", () => {
    function Harness() {
      const [key, setKey] = useState("a");
      return (
        <>
          <button type="button" onClick={() => setKey("b")}>
            cambiar filtros
          </button>
          <ComplianceMatrixTable groups={groups} rows={rows} selectedKey={null} onSelectRow={() => {}} rowCap={3} resetKey={key} />
        </>
      );
    }
    render(<Harness />);
    expect(bodyRows()).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "Mostrar 3 más (4 sin pintar)" }));
    expect(bodyRows()).toHaveLength(6);
    fireEvent.click(screen.getByRole("button", { name: "Mostrar 1 más (1 sin pintar)" }));
    expect(bodyRows()).toHaveLength(7);
    expect(screen.queryByRole("button", { name: /mostrar .* más/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "cambiar filtros" }));
    expect(bodyRows()).toHaveLength(3);
  });

  it("la leyenda explica los glifos y acepta su propia pista", () => {
    render(<ComplianceMatrixLegend hint="Elige una fila." />);
    expect(screen.getByText(/✓ pasa · ✗ falla .* sin evaluar\. Elige una fila\./)).toBeInTheDocument();
  });
});
