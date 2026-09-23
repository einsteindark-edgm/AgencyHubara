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
const usePhotoMock = vi.fn();
const uploadMutateAsync = vi.fn();

vi.mock("@plugins/chats/frontend/entities/order-ref", async () => {
  const actual = await vi.importActual<
    typeof import("@plugins/chats/frontend/entities/order-ref")
  >("@plugins/chats/frontend/entities/order-ref");
  return {
    ...actual,
    useCustomerOrders: (id: string | null) => useCustomerOrdersMock(id),
    useTransitionOrderStage: () => ({ mutateAsync: transitionMutateAsync }),
    useOrderRefPhoto: (id: string | null) => usePhotoMock(id),
    useUploadOrderRefPhoto: () => ({
      mutateAsync: uploadMutateAsync,
      isPending: false,
    }),
  };
});

function Wrapper({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

beforeEach(() => {
  useCustomerOrdersMock.mockReset();
  transitionMutateAsync.mockReset();
  uploadMutateAsync.mockReset();
  usePhotoMock.mockReset();
  usePhotoMock.mockReturnValue({
    isLoading: false,
    data: { photo: null, has_conversation: true, service_window_open: true },
  });
});

function withOrder(order: Record<string, unknown>) {
  useCustomerOrdersMock.mockReturnValue({
    isLoading: false,
    isError: false,
    data: { orders: [order], count: 1 },
  });
}

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

  it("tap en una transición sin datos extra cambia el estado del pedido", async () => {
    transitionMutateAsync.mockResolvedValue({ success: true, current_stage: "preparing" });
    withOrder({ id: "order_01HX", status: "new" });
    render(<ChatsOrdersPanel sessionId="wa_1" />, { wrapper: Wrapper });
    fireEvent.click(screen.getByRole("button", { name: /preparar/i }));
    await waitFor(() =>
      expect(transitionMutateAsync).toHaveBeenCalledWith({
        orderId: "order_01HX",
        stage: "preparing",
      }),
    );
  });

  it("marcar listo pide la foto (opcional) antes de mover: sin foto pasa a Lista igual", async () => {
    transitionMutateAsync.mockResolvedValue({ success: true, current_stage: "ready" });
    withOrder({ id: "order_01HX", status: "preparing" });
    render(<ChatsOrdersPanel sessionId="wa_1" />, { wrapper: Wrapper });
    fireEvent.click(screen.getByRole("button", { name: /marcar listo/i }));
    // El tap NO mueve el pedido: abre el paso de la foto.
    expect(transitionMutateAsync).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/foto del pedido/i)).toBeInTheDocument();
    expect(screen.getByText(/mensaje normal/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /pasar a lista sin foto/i }));
    await waitFor(() =>
      expect(transitionMutateAsync).toHaveBeenCalledWith({
        orderId: "order_01HX",
        stage: "ready",
      }),
    );
    expect(uploadMutateAsync).not.toHaveBeenCalled();
  });

  it("con foto: la sube primero y SOLO si sale bien pasa el pedido a Lista", async () => {
    uploadMutateAsync.mockResolvedValue({ photo: { file_url: "/x.jpg" } });
    transitionMutateAsync.mockResolvedValue({ success: true, current_stage: "ready" });
    withOrder({ id: "order_01HX", status: "preparing" });
    render(<ChatsOrdersPanel sessionId="wa_1" />, { wrapper: Wrapper });
    fireEvent.click(screen.getByRole("button", { name: /marcar listo/i }));
    const file = new File([new Uint8Array([0xff, 0xd8, 0xff])], "p.jpg", { type: "image/jpeg" });
    fireEvent.change(screen.getByLabelText(/foto del pedido/i), { target: { files: [file] } });
    const send = await screen.findByRole("button", { name: /pasar a lista y enviar foto/i });
    await waitFor(() => expect(send).toBeEnabled());
    fireEvent.click(send);
    await waitFor(() =>
      expect(transitionMutateAsync).toHaveBeenCalledWith({
        orderId: "order_01HX",
        stage: "ready",
      }),
    );
    expect(uploadMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ orderId: "order_01HX" }),
    );
  });

  it("si la foto no se pudo subir, el pedido NO se mueve y se ve el error", async () => {
    uploadMutateAsync.mockRejectedValue(new Error("La foto pesa más de 5 MB."));
    withOrder({ id: "order_01HX", status: "preparing" });
    render(<ChatsOrdersPanel sessionId="wa_1" />, { wrapper: Wrapper });
    fireEvent.click(screen.getByRole("button", { name: /marcar listo/i }));
    const file = new File([new Uint8Array([0xff, 0xd8])], "p.jpg", { type: "image/jpeg" });
    fireEvent.change(screen.getByLabelText(/foto del pedido/i), { target: { files: [file] } });
    const send = await screen.findByRole("button", { name: /pasar a lista y enviar foto/i });
    await waitFor(() => expect(send).toBeEnabled());
    fireEvent.click(send);
    expect(await screen.findByRole("alert")).toHaveTextContent(/5 MB/);
    expect(transitionMutateAsync).not.toHaveBeenCalled();
  });

  it("despachar pide el valor del envío y el link: viajan en el mismo cambio de estado", async () => {
    transitionMutateAsync.mockResolvedValue({ success: true, current_stage: "shipping" });
    withOrder({ id: "order_01HX", status: "ready", total_cop: 124500 });
    render(<ChatsOrdersPanel sessionId="wa_1" />, { wrapper: Wrapper });
    fireEvent.click(screen.getByRole("button", { name: /despachar/i }));
    expect(transitionMutateAsync).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/valor del envío/i), { target: { value: "12.000" } });
    // Total en vivo: pedido + envío.
    expect(screen.getByTestId("ship-total")).toHaveTextContent(/136[.,]500/);
    fireEvent.change(screen.getByLabelText(/link de la guía/i), {
      target: { value: "servientrega.com/rastreo?guia=1" },
    });
    fireEvent.click(screen.getByRole("button", { name: /despachar con guía/i }));
    await waitFor(() =>
      expect(transitionMutateAsync).toHaveBeenCalledWith({
        orderId: "order_01HX",
        stage: "shipping",
        shipping_cost: 12000,
        tracking_url: "https://servientrega.com/rastreo?guia=1",
      }),
    );
  });

  it("despachar muestra el pedido sin el envío estimado y suma solo el envío real", () => {
    // Total registrado $ 57.900 = pedido $ 50.000 + envío estimado $ 7.900.
    withOrder({ id: "order_01HX", status: "ready", total_cop: 57900, shipping_cop: 7900 });
    render(<ChatsOrdersPanel sessionId="wa_1" />, { wrapper: Wrapper });
    fireEvent.click(screen.getByRole("button", { name: /despachar/i }));
    expect(screen.getByTestId("ship-order-value")).toHaveTextContent(/50[.,]000/);
    expect(screen.getByTestId("ship-estimate")).toHaveTextContent(/7[.,]900/);
    fireEvent.change(screen.getByLabelText(/valor del envío/i), { target: { value: "12.000" } });
    expect(screen.getByTestId("ship-total")).toHaveTextContent(/62[.,]000/);
  });

  it("despachar sin guía ni valor mueve el pedido igual", async () => {
    transitionMutateAsync.mockResolvedValue({ success: true, current_stage: "shipping" });
    withOrder({ id: "order_01HX", status: "ready" });
    render(<ChatsOrdersPanel sessionId="wa_1" />, { wrapper: Wrapper });
    fireEvent.click(screen.getByRole("button", { name: /despachar/i }));
    fireEvent.click(screen.getByRole("button", { name: /despachar sin guía/i }));
    await waitFor(() =>
      expect(transitionMutateAsync).toHaveBeenCalledWith({
        orderId: "order_01HX",
        stage: "shipping",
      }),
    );
  });

  it("un valor del envío inválido no despacha y explica por qué", () => {
    withOrder({ id: "order_01HX", status: "ready" });
    render(<ChatsOrdersPanel sessionId="wa_1" />, { wrapper: Wrapper });
    fireEvent.click(screen.getByRole("button", { name: /despachar/i }));
    fireEvent.change(screen.getByLabelText(/valor del envío/i), { target: { value: "12,5" } });
    fireEvent.click(screen.getByRole("button", { name: /despachar sin guía/i }));
    expect(screen.getByRole("alert")).toHaveTextContent(/sin decimales/i);
    expect(transitionMutateAsync).not.toHaveBeenCalled();
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
          : Promise.resolve({ success: true, current_stage: "preparing" }),
    );
    useCustomerOrdersMock.mockReturnValue({
      isLoading: false,
      isError: false,
      data: {
        orders: [
          { id: "order_A", status: "new" },
          { id: "order_B", status: "new" },
        ],
        count: 2,
      },
    });
    render(<ChatsOrdersPanel sessionId="wa_1" />, { wrapper: Wrapper });
    const buttons = screen.getAllByRole("button", { name: /preparar|…/i });
    // Tap en A: A queda ocupada (spinner "…"), B sigue habilitada.
    fireEvent.click(buttons[0]);
    const after = screen.getAllByRole("button", { name: /preparar|…/i });
    expect(after[0]).toBeDisabled();
    expect(after[1]).toBeEnabled();
    // Tap en B con A aún en vuelo: B dispara SU mutación.
    fireEvent.click(after[1]);
    await waitFor(() =>
      expect(transitionMutateAsync).toHaveBeenCalledWith({
        orderId: "order_B",
        stage: "preparing",
      }),
    );
    resolveA({ success: true, current_stage: "preparing" });
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
