/**
 * "En camino" de un pedido con envío gratis (6 cajas o más de la campaña;
 * operador, 2026-10-20).
 *
 * El operador igual escribe el valor real del envío (es un gasto de la tienda
 * y queda anotado), pero no se le cobra al cliente: el total a cobrar sigue
 * siendo el de los productos y el modal lo dice.
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
  id: "#41",
  status: "ready",
  customer: "Ana",
  short: "AN",
  color: "a",
  items: 1,
  pieces: 6,
  total: 135_000,
  shipping: 0,
  shippingConfirmed: true,
  freeShipping: true,
  storeShippingCost: null,
  payStatus: "pending",
  payType: "cod",
  isDraft: false,
  overdue: false,
  dueIso: "2026-10-21",
  dueTime: "10:00",
} as unknown as Order;

function dropOnShipping() {
  const column = screen.getByText("En camino").closest(".kcol")!;
  const dataTransfer = { getData: () => "#41", types: ["application/x-hubara-order"] };
  fireEvent.drop(column, { dataTransfer });
}

describe("OrdersBoard — En camino con envío gratis", () => {
  beforeEach(() => mutate.mockReset());

  it("anota el envío real como gasto de la tienda sin sumárselo al cliente", () => {
    render(<OrdersBoard orders={[order]} selectedId={null} onSelect={() => {}} />);
    dropOnShipping();

    expect(screen.getByText(/envío gratis/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/lo paga la tienda/i), { target: { value: "18.000" } });
    expect(screen.getByTestId("ship-total")).toHaveTextContent("$ 135.000");

    fireEvent.click(screen.getByRole("button", { name: /marcar sin guía/i }));
    expect(mutate.mock.calls[0][0]).toMatchObject({ orderId: "#41", to_stage: "shipping", shipping_cost: 18000 });
  });
});
