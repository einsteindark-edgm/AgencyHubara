/**
 * Tests del panel de pedidos del cliente: muestra los pedidos con su estado,
 * ofrece SOLO las transiciones válidas (DAG), cambia el estado con un tap,
 * pide confirmación para cancelar, y muestra errores del backend inline.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { ChatsOrdersPanel } from "./ChatsOrdersPanel";

const useCustomerOrdersMock = vi.fn();
const transitionMutateAsync = vi.fn();

vi.mock("@plugins/chats/frontend/entities/order-ref", async () => {
  const actual = await vi.importActual<
    typeof import("@plugins/chats/frontend/entities/order-ref")
  >("@plugins/chats/frontend/entities/order-ref");
  return {
    ...actual,
    useCustomerOrders: (id: string | null) => useCustomerOrdersMock(id),
    useTransitionOrderStage: () => ({ mutateAsync: transitionMutateAsync }),
  };
});

function Wrapper({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

beforeEach(() => {
  useCustomerOrdersMock.mockReset();
  transitionMutateAsync.mockReset();
});

describe("ChatsOrdersPanel", () => {
  it("estado vacío cuando el cliente no tiene pedidos", () => {
    useCustomerOrdersMock.mockReturnValue({
      isLoading: false,
      isError: false,
      data: { orders: [], count: 0 },
    });
    render(<ChatsOrdersPanel sessionId="wa_1" />, { wrapper: Wrapper });
    expect(screen.getByText(/no tiene pedidos/i)).toBeInTheDocument();
  });

  it("muestra cada pedido con su estado y SOLO las transiciones válidas", () => {
    useCustomerOrdersMock.mockReturnValue({
      isLoading: false,
      isError: false,
      data: {
        orders: [
          { id: "order_01HX", status: "preparing", total_cop: 124500 },
        ],
        count: 1,
      },
    });
    render(<ChatsOrdersPanel sessionId="wa_1" />, { wrapper: Wrapper });
    // Estado actual visible.
    expect(screen.getByText("Preparando")).toBeInTheDocument();
    // preparing → ready (avanzar) + cancelar. NO "Despachar" ni "Entregar".
    expect(screen.getByRole("button", { name: /marcar listo/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^cancelar$/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /despachar/i })).not.toBeInTheDocument();
  });

  it("tap en una transición cambia el estado del pedido", async () => {
    transitionMutateAsync.mockResolvedValue({ success: true, current_stage: "ready" });
    useCustomerOrdersMock.mockReturnValue({
      isLoading: false,
      isError: false,
      data: { orders: [{ id: "order_01HX", status: "preparing" }], count: 1 },
    });
    render(<ChatsOrdersPanel sessionId="wa_1" />, { wrapper: Wrapper });
    fireEvent.click(screen.getByRole("button", { name: /marcar listo/i }));
    await waitFor(() =>
      expect(transitionMutateAsync).toHaveBeenCalledWith({
        orderId: "order_01HX",
        stage: "ready",
      }),
    );
  });

  it("cancelar pide confirmación de dos pasos", async () => {
    transitionMutateAsync.mockResolvedValue({ success: true, current_stage: "cancelled" });
    useCustomerOrdersMock.mockReturnValue({
      isLoading: false,
      isError: false,
      data: { orders: [{ id: "order_01HX", status: "preparing" }], count: 1 },
    });
    render(<ChatsOrdersPanel sessionId="wa_1" />, { wrapper: Wrapper });
    // Primer tap: NO cancela todavía, pide confirmación.
    fireEvent.click(screen.getByRole("button", { name: /^cancelar$/i }));
    expect(transitionMutateAsync).not.toHaveBeenCalled();
    expect(screen.getByText(/¿cancelar el pedido/i)).toBeInTheDocument();
    // Confirmar.
    fireEvent.click(screen.getByRole("button", { name: /sí, cancelar/i }));
    await waitFor(() =>
      expect(transitionMutateAsync).toHaveBeenCalledWith({
        orderId: "order_01HX",
        stage: "cancelled",
      }),
    );
  });

  it("un pedido entregado no ofrece cambios de estado", () => {
    useCustomerOrdersMock.mockReturnValue({
      isLoading: false,
      isError: false,
      data: { orders: [{ id: "order_01HX", status: "delivered" }], count: 1 },
    });
    render(<ChatsOrdersPanel sessionId="wa_1" />, { wrapper: Wrapper });
    expect(screen.getByText(/sin más cambios/i)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("PM2-M4: una transición en vuelo en una tarjeta NO habilita/deshabilita a las demás", async () => {
    let resolveA: (v: unknown) => void = () => {};
    transitionMutateAsync.mockImplementation(
      ({ orderId }: { orderId: string }) =>
        orderId === "order_A"
          ? new Promise((r) => {
              resolveA = r;
            })
          : Promise.resolve({ success: true, current_stage: "ready" }),
    );
    useCustomerOrdersMock.mockReturnValue({
      isLoading: false,
      isError: false,
      data: {
        orders: [
          { id: "order_A", status: "preparing" },
          { id: "order_B", status: "preparing" },
        ],
        count: 2,
      },
    });
    render(<ChatsOrdersPanel sessionId="wa_1" />, { wrapper: Wrapper });
    const buttons = screen.getAllByRole("button", { name: /marcar listo|…/i });
    // Tap en A: A queda ocupada (spinner "…"), B sigue habilitada.
    fireEvent.click(buttons[0]);
    const after = screen.getAllByRole("button", { name: /marcar listo|…/i });
    expect(after[0]).toBeDisabled();
    expect(after[1]).toBeEnabled();
    // Tap en B con A aún en vuelo: B dispara SU mutación.
    fireEvent.click(after[1]);
    await waitFor(() =>
      expect(transitionMutateAsync).toHaveBeenCalledWith({
        orderId: "order_B",
        stage: "ready",
      }),
    );
    resolveA({ success: true, current_stage: "ready" });
  });

  it("PM2-M11: la fecha de entrega se muestra legible, no como ISO crudo", () => {
    useCustomerOrdersMock.mockReturnValue({
      isLoading: false,
      isError: false,
      data: {
        orders: [{ id: "order_01HX", status: "preparing", due_iso: "2026-07-15" }],
        count: 1,
      },
    });
    render(<ChatsOrdersPanel sessionId="wa_1" />, { wrapper: Wrapper });
    expect(screen.queryByText(/2026-07-15/)).not.toBeInTheDocument();
    expect(screen.getByText(/Entrega:/)).toHaveTextContent(/15/);
  });

  it("muestra inline el error del backend en una transición inválida", async () => {
    transitionMutateAsync.mockResolvedValue({
      success: false,
      error_detail: "invalid_transition: necesita fecha de entrega",
    });
    useCustomerOrdersMock.mockReturnValue({
      isLoading: false,
      isError: false,
      data: { orders: [{ id: "order_01HX", status: "new" }], count: 1 },
    });
    render(<ChatsOrdersPanel sessionId="wa_1" />, { wrapper: Wrapper });
    fireEvent.click(screen.getByRole("button", { name: /preparar/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/fecha de entrega/i);
  });
});
