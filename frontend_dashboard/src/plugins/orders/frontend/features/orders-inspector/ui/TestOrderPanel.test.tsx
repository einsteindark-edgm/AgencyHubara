/**
 * "Marcar como prueba" en el inspector de Órdenes (2026-09-21).
 *
 *   - Pedido real → botón "Marcar como prueba" → PATCH con isTest=true.
 *   - Pedido de prueba → aviso "no cuenta en los totales" + "Quitar marca de
 *     prueba" → PATCH con isTest=false.
 *   - Un `success:false` del backend se muestra al operador.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { Order } from "@plugins/orders/frontend/entities/order";

const setTestMutate = vi.fn();

vi.mock("@plugins/orders/frontend/entities/order", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useSetTestOrder: () => ({ mutate: setTestMutate, isPending: false }),
}));

import { TestOrderPanel } from "./TestOrderPanel";

function order(overrides: Partial<Order> = {}): Order {
  return {
    id: "#7",
    customer: "Cliente",
    short: "CL",
    color: "a",
    phone: "—",
    city: "Bogotá",
    channel: "WhatsApp",
    status: "preparing",
    payStatus: "paid",
    payType: "confirmed",
    items: 1,
    total: 50000,
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

beforeEach(() => {
  setTestMutate.mockReset();
});

describe("Marcar como prueba", () => {
  it("un pedido real ofrece marcarlo como prueba", () => {
    render(<TestOrderPanel order={order()} />);
    fireEvent.click(screen.getByRole("button", { name: "Marcar como prueba" }));
    expect(setTestMutate).toHaveBeenCalledWith(
      { orderId: "#7", isTest: true },
      expect.any(Object),
    );
  });

  it("un pedido de prueba avisa que no cuenta y ofrece quitar la marca", () => {
    render(<TestOrderPanel order={order({ isTest: true })} />);
    expect(screen.getByText(/no cuenta en los totales/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Quitar marca de prueba" }));
    expect(setTestMutate).toHaveBeenCalledWith(
      { orderId: "#7", isTest: false },
      expect.any(Object),
    );
  });

  it("muestra el error del backend", () => {
    setTestMutate.mockImplementation((_vars, opts) =>
      opts.onSuccess({ success: false, order_id: "#7", error_detail: "not_found: x" }),
    );
    render(<TestOrderPanel order={order()} />);
    fireEvent.click(screen.getByRole("button", { name: "Marcar como prueba" }));
    expect(screen.getByText("not_found: x")).toBeInTheDocument();
  });
});
