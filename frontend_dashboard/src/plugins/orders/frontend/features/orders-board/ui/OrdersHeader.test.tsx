/**
 * KPIs del encabezado de Órdenes en el borde de las 19:00 hora Colombia.
 *
 * Dos efectos del corte UTC que este archivo fija:
 *   - "Para hoy" saltaba a las 19:00 y pasaba a contar las entregas de mañana.
 *   - "Ingresos mes" deriva de `today.slice(0, 7)`: la noche del último día del
 *     mes, a partir de las 7, el KPI ya sumaba el mes siguiente y el mes que
 *     estaba cerrando aparecía en cero.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { Order } from "@plugins/orders/frontend/entities/order";
import type { DateRange } from "@/shared/lib";
import { OrdersHeader } from "./OrdersBoard";

function order(overrides: Partial<Order> & { id: string }): Order {
  return {
    customer: "Cliente",
    short: "CL",
    color: "a",
    phone: "+57 300 111 2233",
    city: "Bogotá",
    channel: "WhatsApp",
    status: "preparing",
    payStatus: "pending",
    payType: "cod",
    items: 1,
    total: 100_000,
    dueIso: "",
    dueTime: "—",
    pieces: 1,
    agent: "—",
    priority: "normal",
    isDraft: false,
    isDueEstimated: false,
    createdIso: "",
    ...overrides,
  };
}

function renderHeader(
  orders: Order[],
  range: DateRange = { from: null, to: null },
  rangeLabel = "Todas las fechas",
) {
  render(
    <OrdersHeader
      orders={orders}
      filteredCount={orders.length}
      filteredTotal={0}
      title="Órdenes"
      range={range}
      rangeLabel={rangeLabel}
    />,
  );
}

/** Entregada y pagada: lo único que cuenta como ingreso. */
function cobrada(overrides: Partial<Order> & { id: string }): Order {
  return order({ status: "delivered", payStatus: "paid", ...overrides });
}

/** Valor del KPI con esa etiqueta. */
function kpi(label: string): string {
  const tile = screen.getByText(label).closest(".kpi");
  return tile?.querySelector(".kv")?.textContent ?? "";
}

afterEach(() => {
  vi.useRealTimers();
});

describe("a las 20:00 del 10 de septiembre hora Colombia", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-11T01:00:00.000Z"));
  });

  it("'Para hoy' cuenta las entregas de hoy en Colombia", () => {
    renderHeader([
      order({ id: "hoy-1", dueIso: "2026-09-10" }),
      order({ id: "hoy-2", dueIso: "2026-09-10" }),
      order({ id: "manana", dueIso: "2026-09-11" }),
    ]);
    expect(kpi("Para hoy")).toBe("2");
  });
});

describe("a las 20:00 del ÚLTIMO día del mes hora Colombia", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // 2026-09-01T01:00:00Z === 2026-08-31 20:00 en Bogotá.
    vi.setSystemTime(new Date("2026-09-01T01:00:00.000Z"));
  });

  it("'Ingresos mes' sigue sumando AGOSTO, no septiembre", () => {
    renderHeader([
      cobrada({ id: "ago-1", dueIso: "2026-08-15", total: 100_000 }),
      cobrada({ id: "ago-2", dueIso: "2026-08-31", total: 100_000 }),
      cobrada({ id: "sep", dueIso: "2026-09-02", total: 500_000 }),
    ]);
    // 200.000 en agosto. Con el corte UTC el mes ya era septiembre y el KPI
    // mostraba los 500.000 de una entrega que ni siquiera había ocurrido.
    expect(kpi("Ingresos mes")).toContain("200");
  });

  it("'Para hoy' cuenta las del 31 de agosto, no las del 1 de septiembre", () => {
    renderHeader([
      order({ id: "hoy", dueIso: "2026-08-31" }),
      order({ id: "manana", dueIso: "2026-09-01" }),
    ]);
    expect(kpi("Para hoy")).toBe("1");
  });
});

/**
 * Ingresos = plata que YA entró: órdenes de la columna "Entregada" con el pago
 * en "Pagado". Antes sumaba toda orden activa con entrega agendada en el mes —
 * incluidas las que seguían en preparación o sin cobrar—, así que el KPI
 * prometía ingresos que todavía no existían.
 */
describe("'Ingresos' del tablero", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-21T15:00:00.000Z")); // 10:00 Bogotá
  });

  it("sin rango suma solo las ENTREGADAS y PAGADAS del mes en curso", () => {
    renderHeader([
      cobrada({ id: "ok-1", dueIso: "2026-09-05", total: 100_000 }),
      cobrada({ id: "ok-2", dueIso: "2026-09-20", total: 50_000 }),
      // Entregada pero sin cobrar (contra entrega aún no conciliada).
      order({ id: "debe", status: "delivered", payStatus: "pending", dueIso: "2026-09-10", total: 700_000 }),
      // Pagada pero todavía en preparación: no es ingreso del tablero aún.
      order({ id: "prep", status: "preparing", payStatus: "paid", dueIso: "2026-09-22", total: 900_000 }),
      // Entregada y pagada, pero del mes pasado.
      cobrada({ id: "ago", dueIso: "2026-08-30", total: 300_000 }),
    ]);
    expect(kpi("Ingresos mes")).toBe("$ 150 k");
  });

  it("una entregada sin fecha agendada cuenta por el día en que se creó", () => {
    renderHeader([
      cobrada({ id: "sin-fecha", dueIso: "", createdIso: "2026-09-02", total: 80_000 }),
      cobrada({ id: "sin-fecha-ago", dueIso: "", createdIso: "2026-08-28", total: 40_000 }),
    ]);
    expect(kpi("Ingresos mes")).toBe("$ 80 k");
  });

  it("con un rango elegido suma las entregadas y pagadas DENTRO del rango", () => {
    renderHeader(
      [
        cobrada({ id: "antes", dueIso: "2026-09-09", total: 10_000 }),
        cobrada({ id: "borde-1", dueIso: "2026-09-10", total: 20_000 }),
        cobrada({ id: "medio", dueIso: "2026-09-12", total: 30_000 }),
        cobrada({ id: "borde-2", dueIso: "2026-09-15", total: 40_000 }),
        cobrada({ id: "despues", dueIso: "2026-09-16", total: 50_000 }),
        order({ id: "debe", status: "delivered", payStatus: "partial", dueIso: "2026-09-12", total: 999_000 }),
      ],
      { from: "2026-09-10", to: "2026-09-15" },
      "10 – 15 sep 2026",
    );
    // El rótulo deja de decir "mes": el período es el que eligió el operador.
    expect(screen.queryByText("Ingresos mes")).not.toBeInTheDocument();
    expect(kpi("Ingresos")).toBe("$ 90 k");
    expect(screen.getByText(/10 – 15 sep 2026/)).toBeInTheDocument();
  });
});

// El botón "Filtros" era un resto del prototipo sin acción: el operador lo
// clickeaba y "el filtro no funcionaba". Los filtros viven en la sidebar.
describe("acciones del encabezado", () => {
  it("no ofrece un botón 'Filtros' decorativo", () => {
    renderHeader([]);
    expect(screen.queryByRole("button", { name: /filtros/i })).not.toBeInTheDocument();
  });
});
