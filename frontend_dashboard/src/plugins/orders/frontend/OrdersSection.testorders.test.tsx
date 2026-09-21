/**
 * Pedidos de prueba en Órdenes (pedido del operador, 2026-09-21).
 *
 * Hay pedidos que no son ventas reales. El operador los marca "prueba" desde
 * el inspector y desde ahí:
 *   - siguen en el tablero (se pueden revisar, mover, desmarcar);
 *   - NO cuentan en ningún número de la pantalla: contadores de la sidebar,
 *     KPIs del tablero ni el "N órdenes · $ en valor" del encabezado.
 *
 * El tablero y el header se stubean: acá importa QUÉ órdenes y números les llegan.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { Order } from "@plugins/orders/frontend/entities/order";

const orders = vi.hoisted(() => ({ current: [] as Order[] }));

vi.mock("@/shared/lib", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  usePluginHost: () => ({ showSidebar: true, showInspector: false }),
  useSelection: () => [null, () => {}],
}));

vi.mock("@plugins/orders/frontend/entities/order", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useOrders: () => ({
    data: {
      orders: orders.current,
      response: { catalog_available: true, error_detail: null },
    },
    isLoading: false,
  }),
  useVaultOrders: () => ({ data: undefined }),
  useOrdersEvents: () => {},
}));

vi.mock("@plugins/orders/frontend/features/orders-board", () => ({
  OrdersBoard: ({ orders }: { orders: Order[] }) => (
    <ul aria-label="Tablero">
      {orders.map((o) => (
        <li key={o.id}>{o.id}</li>
      ))}
    </ul>
  ),
  OrdersHeader: ({
    orders,
    filteredCount,
    filteredTotal,
  }: {
    orders: Order[];
    filteredCount: number;
    filteredTotal: number;
  }) => (
    <section aria-label="KPIs" data-count={filteredCount} data-total={filteredTotal}>
      {orders.map((o) => (
        <span key={o.id}>{o.id}</span>
      ))}
    </section>
  ),
}));

vi.mock("@plugins/orders/frontend/features/orders-inspector", () => ({
  OrdersInspector: () => null,
}));

vi.mock("@plugins/orders/frontend/features/orders-vault-reconciliation", () => ({
  VaultOrdersBanner: () => null,
}));

import { OrdersSection } from "./OrdersSection";

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
    total: 100000,
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

const ORDERS: Order[] = [
  order({ id: "#1", total: 100000 }),
  order({ id: "#2", total: 50000, status: "preparing", payStatus: "pending" }),
  order({ id: "#3", total: 999000, isTest: true }),
  order({ id: "#4", total: 777000, status: "preparing", isTest: true }),
];

function renderSection() {
  orders.current = ORDERS;
  render(<OrdersSection />);
}

function countOf(label: string): number {
  const row = screen.getByText(label).closest(".of-row");
  return Number(row?.querySelector(".ofn")?.textContent);
}

describe("pedidos de prueba", () => {
  it("siguen en el tablero para poder revisarlos o desmarcarlos", () => {
    renderSection();
    const board = screen.getByRole("list", { name: "Tablero" });
    expect(Array.from(board.children).map((c) => c.textContent)).toEqual([
      "#1",
      "#2",
      "#3",
      "#4",
    ]);
  });

  it("no llegan a los KPIs del tablero", () => {
    renderSection();
    const kpis = screen.getByRole("region", { name: "KPIs" });
    expect(Array.from(kpis.children).map((c) => c.textContent)).toEqual(["#1", "#2"]);
  });

  it("no suman en el 'N órdenes · $ en valor' del encabezado", () => {
    renderSection();
    const kpis = screen.getByRole("region", { name: "KPIs" });
    expect(kpis).toHaveAttribute("data-count", "2");
    expect(kpis).toHaveAttribute("data-total", "150000");
  });

  it("no cuentan en los contadores de la sidebar", () => {
    renderSection();
    expect(countOf("Todas")).toBe(2);
    expect(countOf("En proceso")).toBe(1);
  });
});
