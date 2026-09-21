/**
 * Modal "Lista" — al soltar un pedido en la columna `ready` el operador puede
 * adjuntar la foto del pedido (opcional). Si la sube, el pedido pasa a Lista
 * y el Agente ETA manda la foto al cliente de una vez:
 *  - ventana 24h abierta (el cliente escribió hace < 24 h) → mensaje normal;
 *  - cerrada → plantilla aprobada de pedido listo.
 * El modal le dice al operador cuál de los dos va a pasar. Sin foto, el pedido
 * pasa a Lista con el aviso de siempre. Si la subida falla, NO se mueve.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import type { Order, OrderPhoto } from "@plugins/orders/frontend/entities/order";

const h = vi.hoisted(() => ({
  photo: { data: undefined as OrderPhoto | undefined, isLoading: false },
  uploadAsync: vi.fn(),
  mutate: vi.fn(),
}));

vi.mock("@plugins/orders/frontend/entities/order", async (importOriginal) => {
  const { useMutation } = await import("@tanstack/react-query");
  return {
    ...(await importOriginal<object>()),
    useOrderPhoto: () => h.photo,
    // Mutation REAL sobre un fake: el error/pending salen de TanStack como en prod.
    useUploadOrderPhoto: () =>
      useMutation({ mutationFn: (vars: unknown) => h.uploadAsync(vars) }),
    useTransitionOrderStage: () => ({ mutate: h.mutate, isPending: false }),
  };
});

vi.mock("@/shared/lib", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  compressImage: async (file: File) => ({ blob: file, mime: "image/jpeg", previewUrl: "blob:preview" }),
}));

import { ReadyPhotoModal } from "./ui/ReadyPhotoModal";

const { OrdersBoard } = await import("./ui/OrdersBoard");

function photoState(over: Partial<OrderPhoto> = {}): OrderPhoto {
  return {
    order_id: "order_01",
    has_conversation: true,
    service_window_open: false,
    photo: null,
    ...over,
  };
}

function pick() {
  const file = new File([new Uint8Array([0xff, 0xd8, 0xff])], "vela.jpg", { type: "image/jpeg" });
  fireEvent.change(screen.getByLabelText(/foto del pedido/i), { target: { files: [file] } });
  return file;
}

function withQuery(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

function setup() {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  withQuery(<ReadyPhotoModal orderId="#31" onConfirm={onConfirm} onCancel={onCancel} />);
  return { onConfirm, onCancel };
}

beforeEach(() => {
  h.photo = { data: photoState(), isLoading: false };
  h.uploadAsync.mockReset();
  h.mutate.mockReset();
});

describe("ReadyPhotoModal", () => {
  it("offers the photo as optional and moves without it", () => {
    const { onConfirm } = setup();
    expect(screen.getByRole("dialog")).toHaveTextContent("#31");

    fireEvent.click(screen.getByRole("button", { name: /pasar a lista sin foto/i }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(h.uploadAsync).not.toHaveBeenCalled();
  });

  it("uploads the photo first and only then moves the order", async () => {
    h.uploadAsync.mockResolvedValue(photoState());
    const { onConfirm } = setup();
    const file = pick();
    await screen.findByRole("img", { name: /foto del pedido/i });

    fireEvent.click(screen.getByRole("button", { name: /pasar a lista y enviar foto/i }));

    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
    expect(h.uploadAsync).toHaveBeenCalledWith({ orderId: "#31", file });
  });

  it("does not move the order when the upload fails", async () => {
    h.uploadAsync.mockRejectedValue(new Error("La foto pesa más de 5 MB."));
    const { onConfirm } = setup();
    pick();
    await screen.findByRole("img", { name: /foto del pedido/i });

    fireEvent.click(screen.getByRole("button", { name: /pasar a lista y enviar foto/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/5 MB/);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("with the 24h window open it goes as a normal message", () => {
    h.photo = { data: photoState({ service_window_open: true }), isLoading: false };
    setup();
    expect(screen.getByText(/mensaje normal/i)).toBeInTheDocument();
  });

  it("with the window closed it goes with the approved template", () => {
    setup();
    expect(screen.getByText(/plantilla aprobada/i)).toBeInTheDocument();
  });

  it("reuses a photo already uploaded from the inspector", () => {
    h.photo = {
      data: photoState({
        photo: { file_url: "/api/orders/order-photos/wa_1/order_01?v=5", uploaded_at_ms: 5, sent_at_ms: null },
      }),
      isLoading: false,
    };
    const { onConfirm } = setup();
    expect(screen.getByRole("img", { name: /foto del pedido/i }).getAttribute("src")).toContain(
      "/api/orders/order-photos/wa_1/order_01",
    );
    fireEvent.click(screen.getByRole("button", { name: /pasar a lista y enviar foto/i }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(h.uploadAsync).not.toHaveBeenCalled();
  });

  it("without a WhatsApp conversation it only moves the order", () => {
    h.photo = { data: photoState({ has_conversation: false, service_window_open: null }), isLoading: false };
    setup();
    expect(screen.queryByLabelText(/foto del pedido/i)).not.toBeInTheDocument();
    expect(screen.getByText(/no está ligado a una conversación de whatsapp/i)).toBeInTheDocument();
  });

  it("cancels with the button or Escape without moving", () => {
    const { onCancel, onConfirm } = setup();
    fireEvent.click(screen.getByRole("button", { name: /cancelar/i }));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(2);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});

describe("OrdersBoard — soltar en Lista", () => {
  const order = {
    id: "#31",
    status: "preparing",
    customer: "Camila",
    short: "CA",
    color: "#888",
    items: 1,
    pieces: 1,
    total: 50000,
    payStatus: "paid",
    payType: "transfer",
    isDraft: false,
    overdue: false,
    dueIso: "2026-09-21",
    dueTime: "10:00",
  } as unknown as Order;

  it("asks for the photo before moving to ready", () => {
    withQuery(<OrdersBoard orders={[order]} selectedId={null} onSelect={() => {}} />);
    const column = screen.getByText("Lista").closest(".kcol")!;
    fireEvent.drop(column, { dataTransfer: { getData: () => "#31", types: [] } });

    expect(h.mutate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /pasar a lista sin foto/i }));
    expect(h.mutate.mock.calls[0][0]).toEqual({ orderId: "#31", to_stage: "ready" });
  });
});
