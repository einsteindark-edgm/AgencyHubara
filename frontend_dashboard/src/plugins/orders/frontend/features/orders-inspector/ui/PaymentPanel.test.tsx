/**
 * "Reversar pago" en el inspector de orders — para errores operativos
 * (pedido #32, 2026-09-17: pago confirmado por error).
 *
 *   - Pedido pagado (o con refund de Medusa pendiente de reflejar) → ofrece
 *     reversar; pedido pendiente → no.
 *   - Reversar pide confirmación explícita y manda el motivo.
 *   - Un `success:false` del backend se muestra al operador.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { Order, OrderDetail } from "@plugins/orders/frontend/entities/order";

const reverseMutate = vi.fn();

vi.mock("@plugins/orders/frontend/entities/order", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useConfirmOrderPayment: () => ({ mutate: vi.fn(), isPending: false }),
  useReverseOrderPayment: () => ({ mutate: reverseMutate, isPending: false }),
}));

import { PaymentPanel } from "./PaymentPanel";

function order(overrides: Partial<Order> = {}): Order {
  return {
    id: "#32",
    customer: "Cliente",
    short: "CL",
    color: "a",
    phone: "+57 300 111 2233",
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
    ...overrides,
  };
}

const detail = { payment_method_label: "Pago anticipado (Nequi)" } as OrderDetail;

function renderPanel(o: Order) {
  return render(<PaymentPanel detail={detail} missing={new Set()} order={o} />);
}

beforeEach(() => {
  reverseMutate.mockReset();
});

describe("Reversar pago", () => {
  it("un pedido pendiente no ofrece reversar", () => {
    renderPanel(order({ payStatus: "pending" }));
    expect(screen.queryByRole("button", { name: /reversar pago/i })).toBeNull();
  });

  it.each(["paid", "refund"] as const)("un pedido %s ofrece reversar", (payStatus) => {
    renderPanel(order({ payStatus }));
    expect(screen.getByRole("button", { name: /reversar pago/i })).toBeInTheDocument();
  });

  it("pide confirmación y manda el motivo", () => {
    renderPanel(order());
    fireEvent.click(screen.getByRole("button", { name: /reversar pago/i }));
    expect(reverseMutate).not.toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText(/motivo/i), {
      target: { value: "confirmado por error" },
    });
    fireEvent.click(screen.getByRole("button", { name: /sí, reversar/i }));

    expect(reverseMutate).toHaveBeenCalledWith(
      { orderId: "#32", reason: "confirmado por error" },
      expect.anything(),
    );
  });

  it("muestra el error del backend", () => {
    reverseMutate.mockImplementation((_vars, opts) =>
      opts.onSuccess({ success: false, error_detail: "invalid_state: sin pago" }),
    );
    renderPanel(order());
    fireEvent.click(screen.getByRole("button", { name: /reversar pago/i }));
    fireEvent.click(screen.getByRole("button", { name: /sí, reversar/i }));
    expect(screen.getByText("invalid_state: sin pago")).toBeInTheDocument();
  });
});
