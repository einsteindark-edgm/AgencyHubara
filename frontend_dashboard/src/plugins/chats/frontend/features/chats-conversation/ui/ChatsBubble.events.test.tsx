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

describe("datos de envío del formulario", () => {
  const form: ChatMessageItem = {
    kind: "in",
    time: "15:02",
    text: "[datos de envío recibidos] city=Bogotá; flow_token=shipping_wa_test_1",
    event: {
      kind: "shipping_form",
      receiver_name: "Ana Prueba",
      phone: "3001112233",
      city: "Bogotá",
      neighborhood: "Las Nieves",
      address: "Calle 1 #2-3 apto 4",
      payment_method: "transfer",
      order_total_cop: 45000,
      items_summary: "1× Trilogía del Terror",
      extra: [{ key: "notes", value: "Portería 24h" }],
    },
  };

  it("se lee como tarjeta, no como el marker k=v", () => {
    render(<ChatsBubble message={form} />);
    expect(screen.queryByText(/datos de envío recibidos\]/)).toBeNull();
    expect(screen.queryByText(/flow_token/)).toBeNull();
    expect(screen.getByText("Cliente · Datos de envío")).toBeTruthy();
  });

  it("muestra quién recibe, dónde y qué se paga", () => {
    render(<ChatsBubble message={form} />);
    expect(screen.getByText("Ana Prueba")).toBeTruthy();
    expect(screen.getByText("Calle 1 #2-3 apto 4")).toBeTruthy();
    expect(screen.getByText("Las Nieves · Bogotá")).toBeTruthy();
    expect(screen.getByText("1× Trilogía del Terror")).toBeTruthy();
    expect(screen.getByText("$ 45.000")).toBeTruthy();
    expect(screen.getByText("Transferencia")).toBeTruthy();
    expect(screen.getByText("Portería 24h")).toBeTruthy();
  });

  it("el teléfono se puede llamar", () => {
    render(<ChatsBubble message={form} />);
    const tel = screen.getByRole("link", { name: /300 111 2233/ });
    expect(tel.getAttribute("href")).toBe("tel:3001112233");
  });

  it("un campo que no llegó no se inventa", () => {
    render(
      <ChatsBubble
        message={{
          ...form,
          event: {
            ...form.event!,
            kind: "shipping_form",
            receiver_name: null,
            phone: null,
            neighborhood: null,
            payment_method: null,
            order_total_cop: null,
            items_summary: null,
            extra: [],
          } as ChatMessageItem["event"],
        }}
      />,
    );
    expect(screen.getByText("Bogotá")).toBeTruthy();
    expect(screen.queryByText("Transferencia")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });
});
