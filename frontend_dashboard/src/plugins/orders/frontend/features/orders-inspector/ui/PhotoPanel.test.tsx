/**
 * Panel "Foto del pedido" (inspector de Órdenes, a la derecha).
 *
 * El operador sube la foto del pedido; al pasarlo a "listo" el Agente ETA se
 * la manda al cliente por WhatsApp. "Enviar ahora" la manda ya (con
 * confirmación en dos pasos: es un mensaje al cliente, no se deshace).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Order, OrderPhoto } from "@plugins/orders/frontend/entities/order";

const h = vi.hoisted(() => ({
  photo: { data: undefined as OrderPhoto | undefined, isLoading: false },
  upload: vi.fn(),
  remove: vi.fn(),
  send: vi.fn(),
  uploadState: { isPending: false, isError: false, error: null as Error | null },
  sendState: { isPending: false, isError: false, isSuccess: false, error: null as Error | null },
}));

vi.mock("@plugins/orders/frontend/entities/order", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useOrderPhoto: () => h.photo,
  useUploadOrderPhoto: () => ({ mutate: h.upload, ...h.uploadState }),
  useDeleteOrderPhoto: () => ({ mutate: h.remove, isPending: false, isError: false, error: null }),
  useSendOrderPhoto: () => ({ mutate: h.send, ...h.sendState }),
}));

vi.mock("@/shared/lib", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  compressImage: async (file: File) => ({ blob: file, mime: "image/jpeg", previewUrl: "blob:x" }),
}));

import { PhotoPanel } from "./PhotoPanel";

function order(status: Order["status"]): Order {
  return {
    id: "#31",
    customer: "Camila",
    short: "CA",
    color: "a",
    phone: "—",
    city: "—",
    channel: "WhatsApp",
    status,
    payStatus: "paid",
    payType: "confirmed",
    items: 1,
    total: 1,
    dueIso: "",
    dueTime: "—",
    pieces: 1,
    agent: "—",
    priority: "normal",
    isDraft: false,
    isDueEstimated: false,
    createdIso: "",
  };
}

const WITH_PHOTO: OrderPhoto = {
  order_id: "order_01",
  has_conversation: true,
  photo: { file_url: "/api/orders/order-photos/wa_1/order_01?v=5", uploaded_at_ms: 5, sent_at_ms: null },
};

beforeEach(() => {
  h.photo = { data: { order_id: "order_01", has_conversation: true, photo: null }, isLoading: false };
  h.upload.mockReset();
  h.remove.mockReset();
  h.send.mockReset();
  h.uploadState = { isPending: false, isError: false, error: null };
  h.sendState = { isPending: false, isError: false, isSuccess: false, error: null };
});

describe("PhotoPanel", () => {
  it("offers the upload and explains it goes out when the order is ready", () => {
    render(<PhotoPanel order={order("preparing")} />);

    expect(screen.getByLabelText(/subir foto del pedido/i)).toBeInTheDocument();
    expect(screen.getByText(/al pasar el pedido a listo/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /enviar ahora/i })).not.toBeInTheDocument();
  });

  it("uploads the picked photo for this order", async () => {
    render(<PhotoPanel order={order("preparing")} />);
    const file = new File([new Uint8Array([0xff, 0xd8, 0xff])], "vela.jpg", { type: "image/jpeg" });

    fireEvent.change(screen.getByLabelText(/subir foto del pedido/i), { target: { files: [file] } });

    await waitFor(() => expect(h.upload).toHaveBeenCalledTimes(1));
    expect(h.upload.mock.calls[0][0]).toEqual({ orderId: "#31", file });
  });

  it("shows the photo and sends it now only after confirming", () => {
    h.photo = { data: WITH_PHOTO, isLoading: false };
    render(<PhotoPanel order={order("ready")} />);

    const img = screen.getByRole("img", { name: /foto del pedido/i });
    expect(img.getAttribute("src")).toContain("/api/orders/order-photos/wa_1/order_01?v=5");

    fireEvent.click(screen.getByRole("button", { name: /enviar ahora/i }));
    expect(h.send).not.toHaveBeenCalled();
    expect(screen.getByText(/mandar la foto a camila por whatsapp/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /sí, enviar/i }));
    expect(h.send).toHaveBeenCalledTimes(1);
    expect(h.send.mock.calls[0][0]).toEqual({ orderId: "#31", requestId: expect.any(String) });
  });

  it("says when it was already sent", () => {
    h.photo = {
      data: { ...WITH_PHOTO, photo: { ...WITH_PHOTO.photo!, sent_at_ms: Date.UTC(2026, 8, 21, 15) } },
      isLoading: false,
    };
    render(<PhotoPanel order={order("ready")} />);
    expect(screen.getByText(/enviada al cliente/i)).toBeInTheDocument();
  });

  it("can remove the photo", () => {
    h.photo = { data: WITH_PHOTO, isLoading: false };
    render(<PhotoPanel order={order("preparing")} />);
    fireEvent.click(screen.getByRole("button", { name: /quitar/i }));
    expect(h.remove).toHaveBeenCalledWith({ orderId: "#31" });
  });

  it("without a WhatsApp conversation there is nobody to send it to", () => {
    h.photo = { data: { order_id: "order_01", has_conversation: false, photo: null }, isLoading: false };
    render(<PhotoPanel order={order("preparing")} />);
    expect(screen.getByText(/no está ligado a una conversación de whatsapp/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/subir foto del pedido/i)).not.toBeInTheDocument();
  });

  it("shows why an upload failed", () => {
    h.uploadState = { isPending: false, isError: true, error: new Error("La foto pesa más de 5 MB.") };
    render(<PhotoPanel order={order("preparing")} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/5 MB/);
  });

  it("is not shown for delivered or cancelled orders", () => {
    const { container } = render(<PhotoPanel order={order("delivered")} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("PhotoPanel · el envío lo hace el ETA", () => {
  it("shows why the last send failed", () => {
    h.photo = {
      data: {
        ...WITH_PHOTO,
        photo: { ...WITH_PHOTO.photo!, last_error: "La plantilla aún no está aprobada en Meta." },
      },
      isLoading: false,
    };
    render(<PhotoPanel order={order("ready")} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/no está aprobada en meta/i);
  });

  it("after asking, says it is queued — not that it was sent", () => {
    h.photo = { data: WITH_PHOTO, isLoading: false };
    h.sendState = { isPending: false, isError: false, isSuccess: true, error: null };
    render(<PhotoPanel order={order("ready")} />);
    expect(screen.getByText(/en cola/i)).toBeInTheDocument();
  });
});
