/**
 * «Exportar» en Órdenes (tarjeta del ciclo #18): los dos botones (cabecera y
 * barra lateral) abren el reporte de ventas con el rango que está activo en la
 * vista; las cifras salen de los mismos pedidos (sin pruebas) y se cierran con
 * Escape.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

vi.mock("@plugins/orders/frontend/features/orders-board", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  OrdersBoard: () => null,
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
    status: "preparing",
    payStatus: "pending",
    payType: "cod",
    items: 1,
    total: 10000,
    shipping: 0,
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

// Hoy = 21 de septiembre en Colombia; «Ayer» = 20.
const ORDERS: Order[] = [
  order({ id: "#ayer-1", dueIso: "2026-09-20" }),
  order({ id: "#ayer-2", dueIso: "2026-09-20" }),
  order({ id: "#ayer-prueba", dueIso: "2026-09-20", isTest: true }),
  order({ id: "#hoy", dueIso: "2026-09-21" }),
];

function renderSection() {
  orders.current = ORDERS;
  render(<OrdersSection />);
  return userEvent.setup();
}

function reportCount(): string {
  const dialog = screen.getByRole("dialog", { name: /reporte de ventas/i });
  const row = within(dialog).getByText("Pedidos").closest("[data-metric]");
  return row?.getAttribute("data-value") ?? "";
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-09-21T15:00:00.000Z")); // 10:00 Bogotá
});
afterEach(() => {
  vi.useRealTimers();
});

describe("Exportar en Órdenes", () => {
  it("el botón de la cabecera abre el reporte con todas las fechas", async () => {
    const user = renderSection();
    const header = screen
      .getAllByRole("button", { name: /^exportar$/i })
      .find((b) => b.textContent?.includes("Exportar"));
    await user.click(header!);

    expect(screen.getByRole("dialog", { name: /reporte de ventas/i })).toBeInTheDocument();
    expect(reportCount()).toBe("3");
  });

  it("el de la barra lateral abre el reporte con el rango activo de la vista", async () => {
    const user = renderSection();
    await user.click(screen.getByRole("button", { name: /todas las fechas|filtrar por fecha/i }));
    await user.click(screen.getByRole("button", { name: "Ayer" }));
    await user.click(screen.getByTitle("Exportar"));

    expect(reportCount()).toBe("2");
  });

  it("Escape cierra el reporte", async () => {
    const user = renderSection();
    await user.click(screen.getByTitle("Exportar"));
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog", { name: /reporte de ventas/i })).not.toBeInTheDocument();
  });
});
