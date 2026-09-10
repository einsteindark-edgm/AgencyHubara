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
    ...overrides,
  };
}

function renderHeader(orders: Order[]) {
  render(
    <OrdersHeader
      orders={orders}
      filteredCount={orders.length}
      filteredTotal={0}
      title="Órdenes"
    />,
  );
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
      order({ id: "ago-1", dueIso: "2026-08-15", total: 100_000 }),
      order({ id: "ago-2", dueIso: "2026-08-31", total: 100_000 }),
      order({ id: "sep", dueIso: "2026-09-02", total: 500_000 }),
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
