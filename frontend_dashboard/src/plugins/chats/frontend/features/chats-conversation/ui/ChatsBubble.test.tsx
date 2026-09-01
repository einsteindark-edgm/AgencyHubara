/**
 * ChatsBubble — chip de documento PDF (comprobantes de pago).
 *
 * Un mensaje con `documentUrl` (inbound del cliente u outbound del operador)
 * pinta un link clickeable con el nombre del archivo que abre el PDF en otra
 * pestaña — el operador VERIFICA el comprobante mirándolo, no leyendo un
 * marker de texto.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatsBubble } from "./ChatsBubble";
import type { ChatMessageItem } from "@plugins/chats/frontend/entities/chat";

describe("ChatsBubble — documento PDF", () => {
  it("inbound con documento → link con el nombre del archivo hacia el PDF", () => {
    const m: ChatMessageItem = {
      kind: "in",
      text: "[el cliente envió un documento PDF: comprobante.pdf]",
      time: "10:00",
      documentUrl: "http://localhost:8000/api/dashboard/media/wa_x/doc-9.pdf",
      documentName: "comprobante.pdf",
    };
    render(<ChatsBubble message={m} />);
    const link = screen.getByRole("link", { name: /comprobante\.pdf/i });
    expect(link).toHaveAttribute(
      "href",
      "http://localhost:8000/api/dashboard/media/wa_x/doc-9.pdf",
    );
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("outbound del humano con documento → chip presente en burbuja humana", () => {
    const m: ChatMessageItem = {
      kind: "out",
      author: "human",
      text: "Ahí va el comprobante",
      time: "10:01",
      documentUrl: "http://localhost:8000/api/dashboard/media/wa_x/out-1.pdf",
      documentName: "recibo.pdf",
    };
    render(<ChatsBubble message={m} />);
    expect(screen.getByRole("link", { name: /recibo\.pdf/i })).toBeDefined();
  });

  it("documento sin nombre → label default 'Documento PDF'", () => {
    const m: ChatMessageItem = {
      kind: "in",
      text: "[el cliente envió un documento PDF]",
      documentUrl: "http://localhost:8000/api/dashboard/media/wa_x/doc-2.pdf",
    };
    render(<ChatsBubble message={m} />);
    expect(
      screen.getByRole("link", { name: /documento pdf/i }),
    ).toBeDefined();
  });

  it("mensaje sin documento → sin chip de documento", () => {
    const m: ChatMessageItem = { kind: "in", text: "hola", time: "09:00" };
    render(<ChatsBubble message={m} />);
    expect(screen.queryByRole("link")).toBeNull();
  });
});
