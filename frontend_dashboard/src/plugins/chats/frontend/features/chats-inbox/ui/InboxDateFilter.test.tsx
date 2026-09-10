/**
 * Calendario del inbox. El comportamiento observable que exige el operador:
 * abrir, elegir un día (o un rango), ver marcados los días CON conversaciones,
 * y poder limpiar el filtro sin adivinar.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { InboxDateFilter } from "./InboxDateFilter";

const TODAY = "2026-09-10";
const ACTIVE = new Set(["2026-09-08", "2026-09-10"]);

function setup(overrides: Partial<Parameters<typeof InboxDateFilter>[0]> = {}) {
  const onChange = vi.fn();
  const onClear = vi.fn();
  render(
    <InboxDateFilter
      value={{ from: null, to: null }}
      onChange={onChange}
      onClear={onClear}
      activeDays={ACTIVE}
      today={TODAY}
      label="Todas las fechas"
      {...overrides}
    />,
  );
  return { onChange, onClear };
}

/** El calendario arranca colapsado: la sidebar mide 280px y la bandeja es lo
 *  que el operador vino a ver. */
function open() {
  fireEvent.click(screen.getByRole("button", { name: /todas las fechas|filtrar por fecha/i }));
}

describe("apertura y etiqueta", () => {
  it("arranca cerrado (no hay grilla en el DOM)", () => {
    setup();
    expect(screen.queryByRole("grid")).not.toBeInTheDocument();
  });

  it("el disparador muestra el rango vigente", () => {
    setup({ value: { from: "2026-09-10", to: "2026-09-10" }, label: "Hoy" });
    expect(screen.getByRole("button", { name: /hoy/i })).toBeInTheDocument();
  });

  it("al abrir muestra el mes de HOY cuando no hay rango elegido", () => {
    setup();
    open();
    expect(screen.getByText("Septiembre 2026")).toBeInTheDocument();
  });

  it("al abrir con un rango elegido muestra el mes de ESE rango", () => {
    setup({ value: { from: "2026-06-03", to: "2026-06-05" }, label: "…" });
    fireEvent.click(screen.getAllByRole("button")[0]);
    expect(screen.getByText("Junio 2026")).toBeInTheDocument();
  });
});

describe("selección de días", () => {
  it("clickear un día ancla el rango en ese día", () => {
    const { onChange } = setup();
    open();
    fireEvent.click(screen.getByRole("button", { name: "8 de septiembre de 2026" }));
    expect(onChange).toHaveBeenCalledWith({ from: "2026-09-08", to: null });
  });

  it("clickear el segundo día cierra el rango", () => {
    const { onChange } = setup({ value: { from: "2026-09-08", to: null } });
    fireEvent.click(screen.getAllByRole("button")[0]);
    fireEvent.click(screen.getByRole("button", { name: "10 de septiembre de 2026" }));
    expect(onChange).toHaveBeenCalledWith({ from: "2026-09-08", to: "2026-09-10" });
  });

  it("los días con conversaciones quedan marcados", () => {
    setup();
    open();
    expect(
      screen.getByRole("button", { name: "8 de septiembre de 2026" }),
    ).toHaveAttribute("data-active", "true");
    expect(
      screen.getByRole("button", { name: "9 de septiembre de 2026" }),
    ).toHaveAttribute("data-active", "false");
  });

  it("marca cuál es hoy", () => {
    setup();
    open();
    expect(
      screen.getByRole("button", { name: "10 de septiembre de 2026" }),
    ).toHaveAttribute("data-today", "true");
  });
});

describe("navegación de mes", () => {
  it("el botón anterior retrocede un mes", () => {
    setup();
    open();
    fireEvent.click(screen.getByRole("button", { name: /mes anterior/i }));
    expect(screen.getByText("Agosto 2026")).toBeInTheDocument();
  });

  it("el botón siguiente avanza un mes", () => {
    setup();
    open();
    fireEvent.click(screen.getByRole("button", { name: /mes siguiente/i }));
    expect(screen.getByText("Octubre 2026")).toBeInTheDocument();
  });
});

describe("atajos y limpieza", () => {
  it("'Hoy' selecciona el día de hoy en Colombia", () => {
    const { onChange } = setup();
    open();
    fireEvent.click(screen.getByRole("button", { name: "Hoy" }));
    expect(onChange).toHaveBeenCalledWith({ from: TODAY, to: TODAY });
  });

  it("'Ayer' selecciona el día anterior", () => {
    const { onChange } = setup();
    open();
    fireEvent.click(screen.getByRole("button", { name: "Ayer" }));
    expect(onChange).toHaveBeenCalledWith({ from: "2026-09-09", to: "2026-09-09" });
  });

  it("'7 días' cubre hoy y los seis anteriores", () => {
    const { onChange } = setup();
    open();
    fireEvent.click(screen.getByRole("button", { name: "7 días" }));
    expect(onChange).toHaveBeenCalledWith({ from: "2026-09-04", to: TODAY });
  });

  it("sin rango activo NO se ofrece limpiar (nada que limpiar)", () => {
    setup();
    open();
    expect(screen.queryByRole("button", { name: /limpiar/i })).not.toBeInTheDocument();
  });

  it("con rango activo, limpiar lo quita", () => {
    const { onClear } = setup({ value: { from: "2026-09-08", to: "2026-09-08" } });
    fireEvent.click(screen.getAllByRole("button")[0]);
    fireEvent.click(screen.getByRole("button", { name: /limpiar/i }));
    expect(onClear).toHaveBeenCalled();
  });
});
