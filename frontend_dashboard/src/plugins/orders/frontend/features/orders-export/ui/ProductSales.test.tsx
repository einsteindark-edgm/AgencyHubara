/**
 * «Por producto» cuando la consulta de líneas falla de verdad (respuesta que
 * no pasa el contrato, después del reintento): dice que no se pudo traer, no
 * se queda en «Cargando…» (revisión de la tarjeta del ciclo #49).
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ProductSales } from "./ProductSales";

describe("ProductSales", () => {
  it("con la consulta en error dice que no se pudo traer el detalle", () => {
    render(<ProductSales lines={{ data: undefined, isLoading: false, isError: true }} report={null} />);

    expect(screen.getByText(/no se pudo traer el detalle por producto/i)).toBeInTheDocument();
    expect(screen.queryByText(/cargando/i)).not.toBeInTheDocument();
  });
});
