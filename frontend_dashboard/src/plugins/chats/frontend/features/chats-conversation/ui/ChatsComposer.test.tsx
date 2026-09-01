/**
 * Smoke test del composer cableado al handoff real.
 *
 * Verificamos los dos modos + el botón "Confirmar pago":
 *  - `active_agent_route !== "humano"` → banner "bot gestionando" + botón Intervenir.
 *  - `active_agent_route === "humano"` → textarea + botón Devolver al bot.
 *  - humano + `pending_payment_order_id` → además "💳 Confirmar pago" (y NO
 *    aparece cuando ese campo es null). Clic → confirm-payment mutation con el id.
 *  - humano + `pending_payment_order_id` → además "📅 Asignar fecha": agenda la
 *    entrega (mismo schedule que orders/ReadyForShip) SIN confirmar el pago.
 *
 * Mockeamos `useSession` (entities/session), las mutaciones de handoff
 * (entities/handoff) y `useConfirmOrderPayment` (entities/order-ref local) para aislar
 * el componente de la red.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
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
const orderDetailMock = vi.fn();

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
  uploadHumanMedia: vi.fn(),
}));

// Mock del outbox de fotos: capturamos enqueue y controlamos los items
// renderizados sin tocar compresión/red.
const enqueueMock = vi.fn();
const retryMock = vi.fn();
const removeMock = vi.fn();
let outboxItems: Array<{
  id: string;
  previewUrl: string;
  caption: string;
  status: string;
  progress: number;
  attachmentId?: string;
  error?: string;
  kind?: string;
  filename?: string;
}> = [];

vi.mock("../model/useOutbox", () => ({
  useOutbox: () => ({
    items: outboxItems,
    enqueue: enqueueMock,
    retry: retryMock,
    remove: removeMock,
  }),
}));

// F5.3: ConfirmPaymentAction usa mutateAsync (flujo encadenado con reducer).
vi.mock("@plugins/chats/frontend/entities/order-ref", () => ({
  useConfirmOrderPayment: () => ({
    mutateAsync: confirmPaymentMutate,
    isPending: false,
  }),
  useScheduleOrder: () => ({
    mutateAsync: scheduleOrderMutate,
    isPending: false,
  }),
  useOrderRefDetail: (orderId: string | null, opts?: { enabled?: boolean }) =>
    orderDetailMock(orderId, opts),
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
  enqueueMock.mockReset();
  retryMock.mockReset();
  removeMock.mockReset();
  outboxItems = [];
  // Default: el pedido NO tiene fecha asignada aún (flujo 2 pasos vigente).
  orderDetailMock.mockReset().mockReturnValue({
    data: undefined,
    isLoading: false,
    isFetching: false,
    isError: false,
  });
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

  it("confirmar en el popover agenda primero y luego confirma el pago (mismo order_id)", async () => {
    useSessionMock.mockReturnValue({
      data: {
        active_agent_route: "humano",
        pending_payment_order_id: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
      },
    });
    // schedule.mutateAsync(vars) → simulamos draft→Order OK.
    scheduleOrderMutate.mockResolvedValue({ success: true, error_detail: null });
    confirmPaymentMutate.mockResolvedValue({ success: true, error_detail: null });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText("💳 Confirmar pago")); // abre popover
    // El botón de acción dentro del popover (label "Confirmar pago").
    const actionBtns = screen.getAllByText(/Confirmar pago/);
    fireEvent.click(actionBtns[actionBtns.length - 1]);

    // Paso 2 (async): al éxito del schedule, confirma el pago del mismo pedido.
    await waitFor(() => expect(confirmPaymentMutate).toHaveBeenCalledTimes(1));
    expect(confirmPaymentMutate.mock.calls[0][0]).toEqual({
      orderId: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
    });

    // Paso 1: agendó con una fecha (draft → Order).
    expect(scheduleOrderMutate).toHaveBeenCalledTimes(1);
    const schedArg = scheduleOrderMutate.mock.calls[0][0];
    expect(schedArg.orderId).toBe("order_01KSTZSP8NWZTH2M4Q5GB3XY9Z");
    expect(schedArg.delivery_iso).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("si agendar falla, NO confirma el pago y muestra el error", async () => {
    useSessionMock.mockReturnValue({
      data: {
        active_agent_route: "humano",
        pending_payment_order_id: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
      },
    });
    scheduleOrderMutate.mockResolvedValue({
      success: false,
      error_detail: "medusa_unavailable: 503",
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText("💳 Confirmar pago"));
    const actionBtns = screen.getAllByText(/Confirmar pago/);
    fireEvent.click(actionBtns[actionBtns.length - 1]);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /medusa_unavailable/,
    );
    expect(scheduleOrderMutate).toHaveBeenCalledTimes(1);
    expect(confirmPaymentMutate).not.toHaveBeenCalled();
  });

  // ─── Adjuntar / enviar fotos ───────────────────────────────────────────────

  it("muestra el botón de adjuntar foto en modo humano", () => {
    useSessionMock.mockReturnValue({ data: { active_agent_route: "humano" } });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    expect(
      screen.getByRole("button", { name: /adjuntar foto/i }),
    ).toBeInTheDocument();
  });

  it("el input de archivo acepta JPEG/PNG y PDF (comprobantes)", () => {
    useSessionMock.mockReturnValue({ data: { active_agent_route: "humano" } });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    const input = screen.getByTestId("chat-file-input") as HTMLInputElement;
    expect(input.accept).toBe("image/jpeg,image/png,application/pdf");
    expect(input.multiple).toBe(true);
  });

  it("seleccionar un PDF lo encola en el outbox como cualquier adjunto", () => {
    useSessionMock.mockReturnValue({ data: { active_agent_route: "humano" } });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });

    const input = screen.getByTestId("chat-file-input") as HTMLInputElement;
    const file = new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], "comprobante.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(input, { target: { files: [file] } });

    expect(enqueueMock).toHaveBeenCalledTimes(1);
    const [files] = enqueueMock.mock.calls[0];
    expect(files[0].name).toBe("comprobante.pdf");
  });

  it("un item document del outbox pinta chip con el nombre del archivo (sin <img>)", () => {
    useSessionMock.mockReturnValue({ data: { active_agent_route: "humano" } });
    outboxItems = [
      {
        id: "d1",
        previewUrl: "",
        caption: "",
        status: "uploading",
        progress: 0.4,
        kind: "document",
        filename: "comprobante.pdf",
      },
    ];
    const { container } = render(<ChatsComposer chatId="wa_X" />, {
      wrapper: makeWrapper(),
    });
    expect(screen.getByText("comprobante.pdf")).toBeInTheDocument();
    // PM-08: clase modificadora (no :has(), que falta en webviews viejos).
    expect(container.querySelector(".outbox-item--doc")).not.toBeNull();
  });

  it("seleccionar una foto la encola en el outbox con el texto como caption", () => {
    useSessionMock.mockReturnValue({ data: { active_agent_route: "humano" } });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });

    // El operador escribe un caption y luego adjunta.
    const textarea = screen.getByPlaceholderText(
      /Escribe un mensaje al cliente/i,
    );
    fireEvent.change(textarea, { target: { value: "mirá el color" } });

    const input = screen.getByTestId("chat-file-input") as HTMLInputElement;
    const file = new File([new Uint8Array([1, 2, 3])], "foto.jpg", {
      type: "image/jpeg",
    });
    fireEvent.change(input, { target: { files: [file] } });

    expect(enqueueMock).toHaveBeenCalledTimes(1);
    const [files, caption] = enqueueMock.mock.calls[0];
    expect(files).toHaveLength(1);
    expect(files[0].name).toBe("foto.jpg");
    expect(caption).toBe("mirá el color");
  });

  it("un item fallido del outbox muestra 'Reintentar' y lo cablea a retry", () => {
    useSessionMock.mockReturnValue({ data: { active_agent_route: "humano" } });
    outboxItems = [
      {
        id: "m1",
        previewUrl: "blob:x",
        caption: "",
        status: "failed",
        progress: 0,
        error: "red caída",
      },
    ];
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    const retryBtn = screen.getByRole("button", { name: /reintentar/i });
    fireEvent.click(retryBtn);
    expect(retryMock).toHaveBeenCalledWith("m1");
  });

  // ─── Botón "Asignar fecha" (solo agenda la entrega, NO confirma pago) ──────

  it("NO muestra 'Asignar fecha' si la sesión no tiene pedido pendiente", () => {
    useSessionMock.mockReturnValue({
      data: { active_agent_route: "humano", pending_payment_order_id: null },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    expect(screen.queryByText(/Asignar fecha/)).not.toBeInTheDocument();
  });

  it("muestra 'Asignar fecha' junto a 'Confirmar pago' cuando hay pedido pendiente", () => {
    useSessionMock.mockReturnValue({
      data: {
        active_agent_route: "humano",
        pending_payment_order_id: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
      },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    expect(screen.getByText("📅 Asignar fecha")).toBeInTheDocument();
    expect(screen.getByText("💳 Confirmar pago")).toBeInTheDocument();
    // Ambos popovers arrancan cerrados: no hay form de fecha visible.
    expect(screen.queryByText(/Fecha de entrega/i)).not.toBeInTheDocument();
  });

  it("abre el popover de asignar fecha sin disparar mutaciones", () => {
    useSessionMock.mockReturnValue({
      data: {
        active_agent_route: "humano",
        pending_payment_order_id: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
      },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText("📅 Asignar fecha"));
    expect(screen.getByText(/Fecha de entrega/i)).toBeInTheDocument();
    expect(scheduleOrderMutate).not.toHaveBeenCalled();
    expect(confirmPaymentMutate).not.toHaveBeenCalled();
  });

  it("asignar fecha agenda el pedido con el order_id y NUNCA confirma el pago", async () => {
    useSessionMock.mockReturnValue({
      data: {
        active_agent_route: "humano",
        pending_payment_order_id: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
      },
    });
    scheduleOrderMutate.mockResolvedValue({ success: true, error_detail: null });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText("📅 Asignar fecha")); // abre popover
    // El botón de acción dentro del popover (label "Asignar fecha").
    const actionBtns = screen.getAllByText(/Asignar fecha/);
    fireEvent.click(actionBtns[actionBtns.length - 1]);

    await waitFor(() => expect(scheduleOrderMutate).toHaveBeenCalledTimes(1));
    const arg = scheduleOrderMutate.mock.calls[0][0];
    expect(arg.orderId).toBe("order_01KSTZSP8NWZTH2M4Q5GB3XY9Z");
    expect(arg.delivery_iso).toMatch(/^\d{4}-\d{2}-\d{2}$/);

    // Al éxito el popover se cierra…
    await waitFor(() =>
      expect(screen.queryByText(/Fecha de entrega/i)).not.toBeInTheDocument(),
    );
    // …y el pago queda intacto (esa es la diferencia con "Confirmar pago").
    expect(confirmPaymentMutate).not.toHaveBeenCalled();
  });

  it("si asignar fecha falla, muestra el error y no toca el pago", async () => {
    useSessionMock.mockReturnValue({
      data: {
        active_agent_route: "humano",
        pending_payment_order_id: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
      },
    });
    scheduleOrderMutate.mockResolvedValue({
      success: false,
      error_detail: "medusa_unavailable: 503",
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText("📅 Asignar fecha"));
    const actionBtns = screen.getAllByText(/Asignar fecha/);
    fireEvent.click(actionBtns[actionBtns.length - 1]);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /medusa_unavailable/,
    );
    expect(scheduleOrderMutate).toHaveBeenCalledTimes(1);
    expect(confirmPaymentMutate).not.toHaveBeenCalled();
  });

  // ─── Confirmar pago con fecha YA asignada: no re-agendar ──────────────────

  it("si el pedido YA tiene fecha asignada, Confirmar pago NO agenda — solo confirma", async () => {
    useSessionMock.mockReturnValue({
      data: {
        active_agent_route: "humano",
        pending_payment_order_id: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
      },
    });
    orderDetailMock.mockReturnValue({
      data: { summary: { due_iso: "2026-07-15", due_time: null } },
      isLoading: false,
      isFetching: false,
      isError: false,
    });
    confirmPaymentMutate.mockResolvedValue({ success: true, error_detail: null });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText("💳 Confirmar pago"));

    // El popover muestra la fecha ya agendada (formateada es-CO, PM-009) y
    // NO pide una nueva.
    expect(screen.getByText(/15 de julio de 2026/)).toBeInTheDocument();
    expect(screen.queryByText(/Fecha de entrega/i)).not.toBeInTheDocument();

    const actionBtns = screen.getAllByText(/Confirmar pago/);
    fireEvent.click(actionBtns[actionBtns.length - 1]);

    await waitFor(() => expect(confirmPaymentMutate).toHaveBeenCalledTimes(1));
    expect(confirmPaymentMutate.mock.calls[0][0]).toEqual({
      orderId: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
    });
    // La clave del fix: NO pisa la fecha que el operador ya asignó.
    expect(scheduleOrderMutate).not.toHaveBeenCalled();
  });

  it("sin fecha asignada (detail sin due_iso), Confirmar pago mantiene el flujo de 2 pasos", async () => {
    useSessionMock.mockReturnValue({
      data: {
        active_agent_route: "humano",
        pending_payment_order_id: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
      },
    });
    orderDetailMock.mockReturnValue({
      data: { summary: { due_iso: null, due_time: null } },
      isLoading: false,
      isFetching: false,
      isError: false,
    });
    scheduleOrderMutate.mockResolvedValue({ success: true, error_detail: null });
    confirmPaymentMutate.mockResolvedValue({ success: true, error_detail: null });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText("💳 Confirmar pago"));
    expect(screen.getByText(/Fecha de entrega/i)).toBeInTheDocument();
    const actionBtns = screen.getAllByText(/Confirmar pago/);
    fireEvent.click(actionBtns[actionBtns.length - 1]);

    await waitFor(() => expect(confirmPaymentMutate).toHaveBeenCalledTimes(1));
    expect(scheduleOrderMutate).toHaveBeenCalledTimes(1);
  });

  // ─── Fixes del premortem (PM-001/002/007/008/010) ──────────────────────────

  it("PM-001: mientras verifica el pedido (isFetching), el submit está bloqueado", () => {
    useSessionMock.mockReturnValue({
      data: {
        active_agent_route: "humano",
        pending_payment_order_id: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
      },
    });
    orderDetailMock.mockReturnValue({
      data: undefined,
      isLoading: true,
      isFetching: true,
      isError: false,
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText("💳 Confirmar pago"));
    const submitBtn = screen.getByText(/Verificando pedido/) as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(true);
    fireEvent.click(submitBtn);
    expect(scheduleOrderMutate).not.toHaveBeenCalled();
    expect(confirmPaymentMutate).not.toHaveBeenCalled();
  });

  it("PM-002: si no se pudo verificar la fecha (detail error), el popover lo avisa", () => {
    useSessionMock.mockReturnValue({
      data: {
        active_agent_route: "humano",
        pending_payment_order_id: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
      },
    });
    orderDetailMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      isFetching: false,
      isError: true,
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText("💳 Confirmar pago"));
    // Warning honesto: la protección "no re-agendar" está apagada.
    expect(
      screen.getByText(/No se pudo verificar si ya hay fecha asignada/i),
    ).toBeInTheDocument();
    // El fallback de 2 pasos sigue disponible (con fecha editable).
    expect(screen.getByText(/Fecha de entrega/i)).toBeInTheDocument();
  });

  it("PM-007: abrir un popover cierra el otro — solo un dialog a la vez", () => {
    useSessionMock.mockReturnValue({
      data: {
        active_agent_route: "humano",
        pending_payment_order_id: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
      },
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText("📅 Asignar fecha"));
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    fireEvent.click(screen.getByText("💳 Confirmar pago"));
    const dialogs = screen.getAllByRole("dialog");
    expect(dialogs).toHaveLength(1);
    expect(dialogs[0]).toHaveAccessibleName("Confirmar pago");
  });

  it("PM-008: Asignar fecha muestra la fecha actualmente agendada si existe", () => {
    useSessionMock.mockReturnValue({
      data: {
        active_agent_route: "humano",
        pending_payment_order_id: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
      },
    });
    orderDetailMock.mockReturnValue({
      data: { summary: { due_iso: "2026-07-15", due_time: null } },
      isLoading: false,
      isFetching: false,
      isError: false,
    });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText("📅 Asignar fecha"));
    expect(screen.getByText(/Actualmente agendada/i)).toBeInTheDocument();
    expect(screen.getByText(/15 de julio de 2026/)).toBeInTheDocument();
  });

  it("PM-010: el agendado implícito de Confirmar pago deja rastro en el note", async () => {
    useSessionMock.mockReturnValue({
      data: {
        active_agent_route: "humano",
        pending_payment_order_id: "order_01KSTZSP8NWZTH2M4Q5GB3XY9Z",
      },
    });
    scheduleOrderMutate.mockResolvedValue({ success: true, error_detail: null });
    confirmPaymentMutate.mockResolvedValue({ success: true, error_detail: null });
    render(<ChatsComposer chatId="wa_X" />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByText("💳 Confirmar pago"));
    const actionBtns = screen.getAllByText(/Confirmar pago/);
    fireEvent.click(actionBtns[actionBtns.length - 1]);

    await waitFor(() => expect(scheduleOrderMutate).toHaveBeenCalledTimes(1));
    expect(scheduleOrderMutate.mock.calls[0][0].note).toMatch(/confirmar pago/i);
  });
});
