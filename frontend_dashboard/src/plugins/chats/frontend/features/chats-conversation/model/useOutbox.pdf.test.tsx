/**
 * useOutbox — pipeline de PDFs (comprobantes de pago del operador).
 *
 * Un PDF NO pasa por `compressImage` (es un pipeline de imágenes: canvas +
 * JPEG). Sube los bytes ORIGINALES con su nombre real — el backend usa ese
 * nombre como `filename_display` y el cliente lo ve en WhatsApp. El cap
 * client-side (10 MB, espejo del server) corta antes de gastar red.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { createElement } from "react";

vi.mock("@/shared/lib", async (importOriginal) => {
  const orig = await importOriginal<typeof import("@/shared/lib")>();
  return { ...orig, compressImage: vi.fn() };
});

vi.mock("@plugins/chats/frontend/entities/handoff", () => ({
  uploadHumanMedia: vi.fn(),
}));

vi.mock("@/shared/api/client", () => ({
  apiClient: { post: vi.fn().mockResolvedValue({ ok: true }) },
}));

import { compressImage } from "@/shared/lib";
import { uploadHumanMedia } from "@plugins/chats/frontend/entities/handoff";
import { useOutbox } from "./useOutbox";

const compressMock = vi.mocked(compressImage);
const uploadMock = vi.mocked(uploadHumanMedia);

function wrap({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return createElement(QueryClientProvider, { client: qc }, children);
}

function pdfFile(name = "comprobante.pdf", bytes = 16): File {
  return new File([new Uint8Array(bytes).fill(0x25)], name, {
    type: "application/pdf",
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  uploadMock.mockResolvedValue({
    ok: true,
    attachment_id: "att-pdf",
    media_ref: "/api/dashboard/media/wa_t/out-1.pdf",
  });
});

describe("useOutbox — PDFs", () => {
  it("un PDF NO se comprime: sube el File original con su nombre real", async () => {
    const { result } = renderHook(() => useOutbox("wa_pdf_1"), { wrapper: wrap });
    const file = pdfFile("comprobante banco.pdf");

    await act(async () => {
      await result.current.enqueue([file], "te lo adjunto");
    });
    await waitFor(() => expect(uploadMock).toHaveBeenCalled());

    expect(compressMock).not.toHaveBeenCalled();
    const [chatId, blob, filename] = uploadMock.mock.calls[0];
    expect(chatId).toBe("wa_pdf_1");
    expect(blob).toBe(file);
    expect(filename).toBe("comprobante banco.pdf");
  });

  it("mientras sube, el item es kind=document con su filename (chip de la tira)", async () => {
    // Upload que nunca resuelve → el item queda observable en vuelo.
    uploadMock.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useOutbox("wa_pdf_2"), { wrapper: wrap });

    await act(async () => {
      await result.current.enqueue([pdfFile("recibo.pdf")], "");
    });

    await waitFor(() => expect(result.current.items).toHaveLength(1));
    expect(result.current.items[0]).toMatchObject({
      kind: "document",
      filename: "recibo.pdf",
    });
  });

  it("un PDF de más de 10 MB queda failed sin tocar la red", async () => {
    const { result } = renderHook(() => useOutbox("wa_pdf_3"), { wrapper: wrap });
    const big = pdfFile("gigante.pdf", 10 * 1024 * 1024 + 1);

    await act(async () => {
      await result.current.enqueue([big], "");
    });

    await waitFor(() =>
      expect(result.current.items[0]?.status).toBe("failed"),
    );
    expect(result.current.items[0]?.error).toMatch(/10\s?MB/i);
    expect(uploadMock).not.toHaveBeenCalled();
  });

  it("las fotos siguen pasando por compresión (regresión)", async () => {
    compressMock.mockResolvedValue({
      blob: new Blob([new Uint8Array(4)], { type: "image/jpeg" }),
      previewUrl: "blob:mock",
      mime: "image/jpeg",
    });
    const { result } = renderHook(() => useOutbox("wa_pdf_4"), { wrapper: wrap });
    const img = new File([new Uint8Array(8)], "foto.jpg", { type: "image/jpeg" });

    await act(async () => {
      await result.current.enqueue([img], "");
    });
    await waitFor(() => expect(uploadMock).toHaveBeenCalled());

    expect(compressMock).toHaveBeenCalledOnce();
    const [, , filename] = uploadMock.mock.calls[0];
    expect(filename).toMatch(/\.jpg$/);
  });
});

describe("useOutbox — detección de PDF por type (PM-07)", () => {
  it("un JPEG renombrado a .pdf va por compresión (el type manda)", async () => {
    compressMock.mockResolvedValue({
      blob: new Blob([new Uint8Array(4)], { type: "image/jpeg" }),
      previewUrl: "blob:mock",
      mime: "image/jpeg",
    });
    const { result } = renderHook(() => useOutbox("wa_pdf_5"), { wrapper: wrap });
    const disguised = new File([new Uint8Array(8)], "foto.pdf", {
      type: "image/jpeg",
    });

    await act(async () => {
      await result.current.enqueue([disguised], "");
    });
    await waitFor(() => expect(uploadMock).toHaveBeenCalled());

    expect(compressMock).toHaveBeenCalledOnce();
    const [, , filename] = uploadMock.mock.calls[0];
    expect(filename).toMatch(/\.jpg$/);
  });

  it("un File sin type pero con .pdf en el nombre sí va como documento", async () => {
    const { result } = renderHook(() => useOutbox("wa_pdf_6"), { wrapper: wrap });
    const noType = new File([new Uint8Array(8)], "recibo.pdf", { type: "" });

    await act(async () => {
      await result.current.enqueue([noType], "");
    });
    await waitFor(() => expect(uploadMock).toHaveBeenCalled());

    expect(compressMock).not.toHaveBeenCalled();
    expect(uploadMock.mock.calls[0][2]).toBe("recibo.pdf");
  });
});
