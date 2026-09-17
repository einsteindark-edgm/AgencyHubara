/**
 * ChatsBubble — cita (reply) del cliente.
 *
 * Caso run 541d90e0: "que el velón amor eterno sea este" citando una foto.
 * La burbuja debe mostrar QUÉ se citó (autor + miniatura + texto) para que el
 * operador no tenga que adivinar.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatsBubble } from "./ChatsBubble";
import type { ChatMessageItem } from "@plugins/chats/frontend/entities/chat";

describe("ChatsBubble — cita de un mensaje", () => {
  it("reply a foto del bot → bloque de cita con autor, texto y miniatura", () => {
    const m: ChatMessageItem = {
      kind: "in",
      text: "que el velón amor eterno sea este",
      time: "16:11",
      replyTo: {
        author: "agent",
        text: "Velón Amor Eterno",
        imageUrl: "https://assets.hubara.com.co/amor-eterno.webp",
      },
    };
    render(<ChatsBubble message={m} />);
    const quote = screen.getByRole("figure", { name: /respondiendo a/i });
    expect(quote).toHaveTextContent("Bot");
    expect(quote).toHaveTextContent("Velón Amor Eterno");
    expect(screen.getByRole("img", { name: /imagen citada/i })).toHaveAttribute(
      "src",
      "https://assets.hubara.com.co/amor-eterno.webp",
    );
  });

  it("reply a mensaje propio del cliente → autor 'Cliente'", () => {
    const m: ChatMessageItem = {
      kind: "in",
      text: "este",
      replyTo: { author: "user", text: "me gustaría algo así" },
    };
    render(<ChatsBubble message={m} />);
    const quote = screen.getByRole("figure", { name: /respondiendo a/i });
    expect(quote).toHaveTextContent("Cliente");
    expect(quote).toHaveTextContent("me gustaría algo así");
    expect(screen.queryByRole("img", { name: /imagen citada/i })).toBeNull();
  });

  it("cita no resuelta → aviso de mensaje no disponible", () => {
    const m: ChatMessageItem = {
      kind: "in",
      text: "y esa?",
      replyTo: { author: "unknown" },
    };
    render(<ChatsBubble message={m} />);
    expect(
      screen.getByRole("figure", { name: /respondiendo a/i }),
    ).toHaveTextContent(/mensaje no disponible/i);
  });

  it("mensaje sin cita → sin bloque de cita", () => {
    render(<ChatsBubble message={{ kind: "in", text: "hola" }} />);
    expect(screen.queryByRole("figure")).toBeNull();
  });
});
