/**
 * Hooks de la foto del pedido (panel "Foto del pedido" del inspector).
 * El id que usa la UI es el display (`#31`): va url-encodeado.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const { get, put, post, del } = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  post: vi.fn(),
  del: vi.fn(),
}));

vi.mock("@/shared/api", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  apiClient: { get, put, post, delete: del },
}));

import { useOrderPhoto, useSendOrderPhoto, useUploadOrderPhoto } from "./api";

const PHOTO = {
  order_id: "order_01",
  has_conversation: true,
  photo: {
    file_url: "/api/orders/order-photos/wa_1/order_01?v=5",
    uploaded_at_ms: 5,
    sent_at_ms: null,
  },
};

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  [get, put, post, del].forEach((m) => m.mockReset());
});

describe("foto del pedido", () => {
  it("reads the photo of the displayed order", async () => {
    get.mockResolvedValue(PHOTO);
    const { result } = renderHook(() => useOrderPhoto("#31"), { wrapper: wrapper() });

    await waitFor(() => expect(result.current.data).toEqual(PHOTO));
    expect(get.mock.calls[0][0]).toBe("/api/orders/orders/%2331/photo");
  });

  it("uploads the file as multipart", async () => {
    put.mockResolvedValue(PHOTO);
    const { result } = renderHook(() => useUploadOrderPhoto(), { wrapper: wrapper() });
    const blob = new Blob(["x"], { type: "image/jpeg" });

    await result.current.mutateAsync({ orderId: "#31", file: blob });

    const [path, body] = put.mock.calls[0];
    expect(path).toBe("/api/orders/orders/%2331/photo");
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).get("file")).toBeInstanceOf(Blob);
  });

  it("asks the ETA to send it now, idempotent per click", async () => {
    post.mockResolvedValue({ queued: true, order_id: "order_01" });
    const { result } = renderHook(() => useSendOrderPhoto(), { wrapper: wrapper() });

    await result.current.mutateAsync({ orderId: "#31", requestId: "req-1" });

    expect(post).toHaveBeenCalledWith("/api/orders/orders/%2331/photo/send", {
      request_id: "req-1",
    });
  });
});
