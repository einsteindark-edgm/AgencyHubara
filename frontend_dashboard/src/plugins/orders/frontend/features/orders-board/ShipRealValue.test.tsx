/**
 * "En camino" con el valor REAL del envío (operador, 2026-09-23).
 *
 * El envío que se registra al vender es una tarifa mínima estimada y ya viene
 * dentro del total del pedido. Al soltar el pedido en "En camino" el modal
 * muestra el valor del pedido SIN ese estimado; el operador escribe el envío
 * real y el total es pedido + envío real (nunca pedido + estimado + real).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import type { Order } from "@plugins/orders/frontend/entities/order";

const mutate = vi.fn();

vi.mock("@plugins/orders/frontend/entities/order", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@plugins/orders/frontend/entities/order")>();
  return {
    ...actual,
    useTransitionOrderStage: () => ({ mutate, isPending: false }),
  };
});

const { OrdersBoard } = await import("./ui/OrdersBoard");

const order = {
  id: "#40",
  status: "ready",
  customer: "Ana",
  short: "AN",
  color: "a",
  items: 1,
  pieces: 1,
  total: 57_900,
  shipping: 7_900,
  shippingConfirmed: false,
  payStatus: "pending",
  payType: "cod",
  isDraft: false,
  overdue: false,
  dueIso: "2026-09-23",
  dueTime: "10:00",
} as unknown as Order;

function dropOnShipping() {
  const column = screen.getByText("En camino").closest(".kcol")!;
  const dataTransfer = { getData: () => "#40", types: ["application/x-hubara-order"] };
  fireEvent.drop(column, { dataTransfer });
}

describe("OrdersBoard — soltar en En camino", () => {
  beforeEach(() => mutate.mockReset());

  it("muestra el valor del pedido sin el envío estimado y suma solo el real", () => {
    render(<OrdersBoard orders={[order]} selectedId={null} onSelect={() => {}} />);
    dropOnShipping();

    expect(screen.getByTestId("ship-order-value")).toHaveTextContent("$ 50.000");
    expect(screen.getByTestId("ship-estimate")).toHaveTextContent("$ 7.900");

    fireEvent.change(screen.getByLabelText(/valor del envío/i), { target: { value: "12.000" } });
    expect(screen.getByTestId("ship-total")).toHaveTextContent("$ 62.000");

    fireEvent.click(screen.getByRole("button", { name: /marcar sin guía/i }));
    expect(mutate.mock.calls[0][0]).toMatchObject({
      orderId: "#40",
      to_stage: "shipping",
      shipping_cost: 12000,
    });
  });
});
