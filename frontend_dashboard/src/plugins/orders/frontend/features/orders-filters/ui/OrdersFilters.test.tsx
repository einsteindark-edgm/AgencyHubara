/**
 * Contadores de las vistas de Órdenes en el borde de las 19:00 hora Colombia.
 *
 * El reloj se congela a las 20:00 del 10 de septiembre en Bogotá (01:00Z del
 * 11). A esa hora el día UTC ya es el 11: con el corte viejo, "Para hoy"
 * contaba las entregas de mañana, "Mañana" las de pasado mañana y la ventana
 * de "Esta semana" corría un día completo.
 *
 * `dueIso` es un día calendario que el operador elige a mano — compararlo
 * contra el día colombiano es la única lectura correcta.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { Order } from "@plugins/orders/frontend/entities/order";
import { OrdersFilters } from "./OrdersFilters";

const VEINTE_HORAS_BOGOTA = new Date("2026-09-11T01:00:00.000Z");
const HOY_COLOMBIA = "2026-09-10";
const MANANA_COLOMBIA = "2026-09-11";

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
    total: 89000,
    dueIso: HOY_COLOMBIA,
    dueTime: "—",
    pieces: 1,
    agent: "—",
    priority: "normal",
    isDraft: false,
    isDueEstimated: false,
    ...overrides,
  };
}

/** Dos entregas hoy en Colombia, una mañana, una la semana que viene. */
const ORDERS: Order[] = [
  order({ id: "hoy-1", dueIso: HOY_COLOMBIA }),
  order({ id: "hoy-2", dueIso: HOY_COLOMBIA }),
  order({ id: "manana", dueIso: MANANA_COLOMBIA }),
  order({ id: "semana", dueIso: "2026-09-15" }),
  order({ id: "fuera-de-semana", dueIso: "2026-09-30" }),
];

/** Lee el contador que acompaña a una vista de la sidebar. */
function countOf(label: string): number {
  const row = screen.getByText(label).closest(".of-row");
  return Number(row?.querySelector(".ofn")?.textContent);
}

function renderFilters(orders: Order[] = ORDERS) {
  render(
    <OrdersFilters
      view="all"
      setView={vi.fn()}
      payType="all"
      setPayType={vi.fn()}
      orders={orders}
    />,
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(VEINTE_HORAS_BOGOTA);
});
afterEach(() => {
  vi.useRealTimers();
});

describe("vistas por fecha a las 20:00 hora Colombia", () => {
  it("'Para hoy' cuenta las entregas de HOY en Colombia, no las de mañana", () => {
    renderFilters();
    expect(countOf("Para hoy")).toBe(2);
  });

  it("'Mañana' cuenta las del día siguiente colombiano", () => {
    renderFilters();
    expect(countOf("Mañana")).toBe(1);
  });

  it("'Esta semana' arranca HOY y cubre siete días", () => {
    // 10 al 16 de septiembre: hoy-1, hoy-2, manana y semana(15). El 30 queda
    // fuera. Con el corte UTC la ventana era 11–17 y perdía las dos de hoy.
    renderFilters();
    expect(countOf("Esta semana")).toBe(4);
  });

  it("una entrega de hoy no se cuenta como de mañana", () => {
    renderFilters([order({ id: "solo-hoy", dueIso: HOY_COLOMBIA })]);
    expect(countOf("Para hoy")).toBe(1);
    expect(countOf("Mañana")).toBe(0);
  });
});

describe("reglas que no cambian", () => {
  it("las canceladas no suman a los contadores operacionales", () => {
    renderFilters([
      order({ id: "viva", dueIso: HOY_COLOMBIA }),
      order({ id: "cancelada", dueIso: HOY_COLOMBIA, status: "cancelled" }),
    ]);
    expect(countOf("Para hoy")).toBe(1);
    expect(countOf("Todas")).toBe(1);
  });

  it("'Sin agendar' son las que no tienen fecha", () => {
    renderFilters([
      order({ id: "sin-fecha", dueIso: "" }),
      order({ id: "con-fecha", dueIso: HOY_COLOMBIA }),
    ]);
    expect(countOf("Sin agendar")).toBe(1);
  });

  it("'Retrasadas' sigue viniendo del backend, no se recalcula acá", () => {
    // Una orden vencida SIN el flag del backend no debe contarse: el mapper
    // del frontend es puro respecto al reloj (F0.5).
    renderFilters([
      order({ id: "vieja-sin-flag", dueIso: "2026-01-01" }),
      order({ id: "marcada", dueIso: "2026-01-01", overdue: true }),
    ]);
    expect(countOf("Retrasadas")).toBe(1);
  });
});
