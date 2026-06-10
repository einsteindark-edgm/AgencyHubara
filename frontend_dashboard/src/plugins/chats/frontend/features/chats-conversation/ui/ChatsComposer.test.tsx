/**
 * Smoke test del composer cableado al handoff real.
 *
 * Verificamos los dos modos + el botón "Confirmar pago":
 *  - `active_agent_route !== "humano"` → banner "bot gestionando" + botón Intervenir.
 *  - `active_agent_route === "humano"` → textarea + botón Devolver al bot.
 *  - humano + `pending_payment_order_id` → además "💳 Confirmar pago" (y NO
 *    aparece cuando ese campo es null). Clic → confirm-payment mutation con el id.
 *
 * Mockeamos `useSession` (entities/session), las mutaciones de handoff
 * (entities/handoff) y `useConfirmOrderPayment` (entities/order-ref local) para aislar
 * el componente de la red.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { ChatsComposer } from "./ChatsComposer";

const useSessionMock = vi.fn();

vi.mock("@plugins/chats/frontend/entities/session", async () => {
  const actual = await vi.importActual<typeof import("@plugins/chats/frontend/entities/session")>(
    "@plugins/chats/frontend/entities/session",
  );
  return {
    ...actual,
    useSession: (id: string | null) => useSessionMock(id),
  };
});

const interveneMutate = vi.fn();
const sendMutate = vi.fn();
const returnMutate = vi.fn();
const confirmPaymentMutate = vi.fn();
const scheduleOrderMutate = vi.fn();

vi.mock("@plugins/chats/frontend/entities/handoff", () => ({
  useInterveneMutation: () => ({
    mutate: interveneMutate,
    isPending: false,
    isError: false,
    error: null,
  }),
  useSendHumanMessageMutation: () => ({
    mutate: sendMutate,
    isPending: false,
    isError: false,
    error: null,
  }),
  useReturnToBotMutation: () => ({
    mutate: returnMutate,
    isPending: false,
    isError: false,
    error: null,
  }),
}));

vi.mock("@plugins/chats/frontend/entities/order-ref", () => ({
  useConfirmOrderPayment: () => ({
    mutate: confirmPaymentMutate,
    isPending: false,
  }),
  useScheduleOrder: () => ({
    mutate: scheduleOrderMutate,
    isPending: false,
  }),
}));

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  useSessionMock.mockReset();
  interveneMutate.mockReset();
  sendMutate.mockReset();
  returnMutate.mockReset();
  confirmPaymentMutate.mockReset();
  scheduleOrderMutate.mockReset();
});

describe("ChatsComposer", () => {
  it("renders 'bot managing' banner when route is ventas", () => {
    useSessionMock.mockReturnValue({
      data: { active_agent_route: "ventas" },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    expect(
      screen.getByText(/está gestionando esta conversación/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /intervenir/i }),
    ).toBeInTheDocument();
  });

  it("clicking Intervenir calls intervene mutation", () => {
    useSessionMock.mockReturnValue({
      data: { active_agent_route: "ventas" },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByRole("button", { name: /intervenir/i }));
    expect(interveneMutate).toHaveBeenCalledWith({});
  });

  it("renders textarea + return-to-bot when route is humano", () => {
    useSessionMock.mockReturnValue({
      data: { active_agent_route: "humano" },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    expect(
      screen.getByPlaceholderText(/Escribe un mensaje al cliente/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Devolver al bot/i)).toBeInTheDocument();
  });

  it("typing + send calls sendMessage mutation with text", () => {
    useSessionMock.mockReturnValue({
      data: { active_agent_route: "humano" },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    const textarea = screen.getByPlaceholderText(
      /Escribe un mensaje al cliente/i,
    ) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "hola humano" } });
    const sendBtn = screen.getByTitle(/Enviar/i);
    fireEvent.click(sendBtn);
    expect(sendMutate).toHaveBeenCalled();
    expect(sendMutate.mock.calls[0][0]).toEqual({ text: "hola humano" });
  });

  it("clicking Devolver al bot opens picker modal", () => {
    useSessionMock.mockReturnValue({
      data: { active_agent_route: "humano" },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText(/Devolver al bot/i));
    expect(screen.getByText(/A qué bot quieres devolver/i)).toBeInTheDocument();
    expect(screen.getByText(/Sales/)).toBeInTheDocument();
    expect(screen.getByText(/Remarketing/)).toBeInTheDocument();
  });

  it("picker → ventas confirm calls returnToBot with target=ventas", () => {
    useSessionMock.mockReturnValue({
      data: { active_agent_route: "humano" },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText(/Devolver al bot/i));
    fireEvent.click(screen.getByText("Confirmar"));
    expect(returnMutate).toHaveBeenCalled();
    expect(returnMutate.mock.calls[0][0].target_route).toBe("ventas");
  });

  it("picker → remarketing requires motivo before confirm", () => {
    useSessionMock.mockReturnValue({
      data: { active_agent_route: "humano" },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText(/Devolver al bot/i));
    fireEvent.click(screen.getByLabelText(/Remarketing/i));
    const confirmBtn = screen.getByText("Confirmar") as HTMLButtonElement;
    expect(confirmBtn.disabled).toBe(true);
    const motivoBox = screen.getByPlaceholderText(/Motivo del gancho/i);
    fireEvent.change(motivoBox, {
      target: { value: "cliente indeciso, retomar suave" },
    });
    expect((screen.getByText("Confirmar") as HTMLButtonElement).disabled).toBe(
      false,
    );
    fireEvent.click(screen.getByText("Confirmar"));
    expect(returnMutate).toHaveBeenCalled();
    const arg = returnMutate.mock.calls[0][0];
    expect(arg.target_route).toBe("remarketing");
    expect(arg.motivo).toMatch(/indeciso/);
  });

  // ─── Botón "Confirmar pago" (HU: confirmar pago desde el chat) ─────────────

  it("NO muestra 'Confirmar pago' si la sesión no tiene pedido pendiente", () => {
    useSessionMock.mockReturnValue({
      data: { active_agent_route: "humano", pending_payment_order_id: null },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    expect(screen.queryByText(/Confirmar pago/)).not.toBeInTheDocument();
  });

  it("muestra 'Confirmar pago' cuando hay pending_payment_order_id", () => {
    useSessionMock.mockReturnValue({
      data: {
        active_agent_route: "humano",
        pending_payment_order_id: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
      },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    expect(screen.getByText(/Confirmar pago/)).toBeInTheDocument();
    // El popover arranca cerrado: no hay form de fecha hasta abrir.
    expect(screen.queryByText(/Fecha de entrega/i)).not.toBeInTheDocument();
  });

  it("abre el popover de agendar+confirmar al clickear el botón", () => {
    useSessionMock.mockReturnValue({
      data: {
        active_agent_route: "humano",
        pending_payment_order_id: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
      },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText("💳 Confirmar pago"));
    expect(screen.getByText(/Fecha de entrega/i)).toBeInTheDocument();
    // Abrir el popover NO debe disparar ninguna mutación todavía.
    expect(scheduleOrderMutate).not.toHaveBeenCalled();
    expect(confirmPaymentMutate).not.toHaveBeenCalled();
  });

  it("confirmar en el popover agenda primero y luego confirma el pago (mismo order_id)", () => {
    useSessionMock.mockReturnValue({
      data: {
        active_agent_route: "humano",
        pending_payment_order_id: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
      },
    });
    // schedule.mutate(vars, { onSuccess }) → simulamos draft→Order OK.
    scheduleOrderMutate.mockImplementation((_vars, opts) => {
      opts?.onSuccess?.({ success: true, error_detail: null });
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText("💳 Confirmar pago")); // abre popover
    // El botón de acción dentro del popover (label "Confirmar pago").
    const actionBtns = screen.getAllByText(/Confirmar pago/);
    fireEvent.click(actionBtns[actionBtns.length - 1]);

    // Paso 1: agendó con una fecha (draft → Order).
    expect(scheduleOrderMutate).toHaveBeenCalledTimes(1);
    const schedArg = scheduleOrderMutate.mock.calls[0][0];
    expect(schedArg.orderId).toBe("order_01KSTZSP8NWZTH2M4Q5GB3XY9Z");
    expect(schedArg.delivery_iso).toMatch(/^\d{4}-\d{2}-\d{2}$/);

    // Paso 2: al éxito del schedule, confirma el pago del mismo pedido.
    expect(confirmPaymentMutate).toHaveBeenCalledTimes(1);
    expect(confirmPaymentMutate.mock.calls[0][0]).toEqual({
      orderId: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
    });
  });

  it("si agendar falla, NO confirma el pago y muestra el error", () => {
    useSessionMock.mockReturnValue({
      data: {
        active_agent_route: "humano",
        pending_payment_order_id: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
      },
    });
    scheduleOrderMutate.mockImplementation((_vars, opts) => {
      opts?.onSuccess?.({
        success: false,
        error_detail: "medusa_unavailable: 503",
      });
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText("💳 Confirmar pago"));
    const actionBtns = screen.getAllByText(/Confirmar pago/);
    fireEvent.click(actionBtns[actionBtns.length - 1]);

    expect(scheduleOrderMutate).toHaveBeenCalledTimes(1);
    expect(confirmPaymentMutate).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/medusa_unavailable/);
  });
});
