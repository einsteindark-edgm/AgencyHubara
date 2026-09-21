/**
 * Kanban con pedidos de prueba (2026-09-21): la card sigue en su columna con
 * la etiqueta "Prueba", pero el contador y el total de la columna —y
 * "Ingresos" del encabezado— solo suman pedidos reales.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { Order } from "@plugins/orders/frontend/entities/order";

vi.mock("@plugins/orders/frontend/entities/order", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useTransitionOrderStage: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { OrdersBoard, OrdersHeader } from "./OrdersBoard";

function order(overrides: Partial<Order> & { id: string }): Order {
  return {
    customer: "Cliente",
    short: "CL",
    color: "a",
    phone: "—",
    city: "Bogotá",
    channel: "WhatsApp",
    status: "delivered",
    payStatus: "paid",
    payType: "confirmed",
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

function column(label: string): HTMLElement {
  return screen.getByText(label, { selector: ".kc-l" }).closest(".kcol") as HTMLElement;
}

describe("columna del kanban con un pedido de prueba", () => {
  it("cuenta y suma solo los pedidos reales, y marca la card de prueba", () => {
    render(
      <OrdersBoard
        orders={[
          order({ id: "#1", total: 100_000 }),
          order({ id: "#2", total: 900_000, isTest: true }),
        ]}
        selectedId={null}
        onSelect={() => {}}
      />,
    );
    const col = column("Entregada");
    expect(col.querySelector(".kc-n")?.textContent).toBe("1");
    expect(col.querySelector(".kc-t")?.textContent).toBe("$ 100 k");
    const testCard = within(col).getByText("#2").closest(".kcard") as HTMLElement;
    expect(within(testCard).getByText("Prueba")).toBeInTheDocument();
    const realCard = within(col).getByText("#1").closest(".kcard") as HTMLElement;
    expect(within(realCard).queryByText("Prueba")).toBeNull();
  });
});

describe("encabezado", () => {
  it("'Ingresos' no suma un pedido de prueba entregado y pagado", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-09-21T15:00:00.000Z"));
    render(
      <OrdersHeader
        orders={[
          order({ id: "#1", dueIso: "2026-09-10", total: 100_000 }),
          order({ id: "#2", dueIso: "2026-09-10", total: 900_000, isTest: true }),
        ]}
        filteredCount={1}
        filteredTotal={100_000}
        title="Órdenes"
        range={{ from: null, to: null }}
        rangeLabel="Todas las fechas"
      />,
    );
    const tile = screen.getByText("Ingresos mes").closest(".kpi");
    expect(tile?.querySelector(".kv")?.textContent).toContain("100");
    expect(tile?.querySelector(".kv")?.textContent).not.toContain("1.000");
    vi.useRealTimers();
  });
});
