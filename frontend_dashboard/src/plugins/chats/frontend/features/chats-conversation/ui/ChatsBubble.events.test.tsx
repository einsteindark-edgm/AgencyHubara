/**
 * ChatsBubble — cada evento se pinta como el mensaje que realmente fue.
 *
 * Antes: una línea de texto plano con el marker crudo ("[el cliente tocó el
 * botón: Ver catálogo]", "🔘 El bot envió botones: …"). El operador tenía que
 * decodificar a mano qué fue un botón, qué escribió la persona y qué describió
 * la IA. Ahora los botones son botones, la foto es una foto y el caption es el
 * caption.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatsBubble } from "./ChatsBubble";
import type { ChatMessageItem } from "@plugins/chats/frontend/entities/chat";

describe("botones del bot", () => {
  const buttons: ChatMessageItem = {
    kind: "out",
    author: "bot",
    time: "13:18",
    event: {
      kind: "bot_buttons",
      body: "Buenas tardes. ¿Qué te gustaría saber?",
      buttons: [
        { title: "Ver catálogo", touched: true },
        { title: "Asesoría" },
        { title: "Envíos y pagos" },
      ],
    },
  };

  it("el mensaje del bot se lee como mensaje, no como marker", () => {
    render(<ChatsBubble message={buttons} />);
    expect(screen.getByText("Buenas tardes. ¿Qué te gustaría saber?")).toBeTruthy();
    expect(screen.queryByText(/El bot envió botones/)).toBeNull();
  });

  it("cada botón es un botón, en su orden", () => {
    render(<ChatsBubble message={buttons} />);
    const titles = screen
      .getAllByTestId("wa-button")
      .map((b) => b.textContent?.replace("Tocado", "").trim());
    expect(titles).toEqual(["Ver catálogo", "Asesoría", "Envíos y pagos"]);
  });

  it("el botón que el cliente tocó queda marcado dentro del mensaje original", () => {
    render(<ChatsBubble message={buttons} />);
    const touched = screen.getByLabelText("Ver catálogo, el cliente tocó este botón");
    expect(touched.textContent).toContain("Tocado");
  });

  it("etiqueta de tipo sobre la burbuja", () => {
    render(<ChatsBubble message={buttons} />);
    expect(screen.getByText("Bot · Botones de respuesta")).toBeTruthy();
  });
});

describe("tap del cliente", () => {
  it("el clic se resume en un chip de una línea, no en una burbuja", () => {
    const m: ChatMessageItem = {
      kind: "in",
      time: "13:18",
      event: { kind: "button_tap", title: "Ver catálogo" },
    };
    const { container } = render(<ChatsBubble message={m} />);
    expect(container.querySelector(".wa-chip-tap")).toBeTruthy();
    expect(container.querySelector(".bubble")).toBeNull();
    expect(screen.getByText("Ver catálogo")).toBeTruthy();
  });
});

describe("foto del cliente", () => {
  const photo: ChatMessageItem = {
    kind: "in",
    time: "13:18",
    imageUrl: "http://localhost:8000/api/dashboard/media/wa_x/1.jpg",
    text: '[el cliente envió una foto: Vela con figura de pareja.] con el texto: "Precio?"',
    event: {
      kind: "customer_photo",
      vision: "Vela con figura de pareja.",
      caption: "Precio?",
      receipt: false,
    },
  };

  it("lo único que escribió la persona es el caption — el marker crudo no se ve", () => {
    render(<ChatsBubble message={photo} />);
    expect(screen.getByText("Precio?")).toBeTruthy();
    expect(screen.queryByText(/el cliente envió una foto/)).toBeNull();
  });

  it("la descripción de la IA va en su propio bloque etiquetado", () => {
    render(<ChatsBubble message={photo} />);
    const vision = screen.getByLabelText("Descripción de la IA");
    expect(vision.textContent).toContain("Visión IA");
    expect(vision.textContent).toContain("Vela con figura de pareja.");
  });

  it("la foto sigue siendo una foto", () => {
    render(<ChatsBubble message={photo} />);
    expect(screen.getByRole("img")).toHaveAttribute(
      "src",
      "http://localhost:8000/api/dashboard/media/wa_x/1.jpg",
    );
  });

  it("visión fallida: sin bloque morado inventado", () => {
    render(
      <ChatsBubble
        message={{ ...photo, event: { ...photo.event!, vision: null } as never }}
      />,
    );
    expect(screen.queryByLabelText("Descripción de la IA")).toBeNull();
    expect(screen.getByText("Precio?")).toBeTruthy();
  });
});

describe("reacciones", () => {
  it("se ve QUÉ emoji mandó el cliente", () => {
    render(
      <ChatsBubble
        message={{
          kind: "in",
          time: "13:20",
          event: { kind: "reaction", emoji: "❤️", author: "user" },
        }}
      />,
    );
    expect(screen.getByText("❤️")).toBeTruthy();
    expect(screen.getByText(/El cliente reaccionó/)).toBeTruthy();
  });

  it("historial viejo sin emoji: se dice que reaccionó, no se inventa cuál", () => {
    const { container } = render(
      <ChatsBubble
        message={{
          kind: "in",
          time: "13:20",
          event: { kind: "reaction", emoji: null, author: "user" },
        }}
      />,
    );
    expect(screen.getByText(/El cliente reaccionó/)).toBeTruthy();
    expect(container.querySelector(".wa-chip-emoji")).toBeNull();
  });

  it("la del bot se distingue de la del cliente", () => {
    render(
      <ChatsBubble
        message={{
          kind: "system",
          time: "13:21",
          event: { kind: "reaction", emoji: "🤍", author: "bot" },
        }}
      />,
    );
    expect(screen.getByText(/El bot reaccionó/)).toBeTruthy();
  });
});
