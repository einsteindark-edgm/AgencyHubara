/**
 * Doble check de WhatsApp junto a la hora de cada mensaje saliente (del bot o
 * del operador). Sale SOLO de lo que WhatsApp informó por webhook: enviado ✓,
 * entregado ✓✓, leído ✓✓ (otro color) y fallido con el código de Meta. Sin
 * dato, «sin confirmación» — antes el panel pintaba «leído» fijo en todos.
 * Caso 2026-10-15: el operador no podía saber si los mensajes llegaron.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ChatMessageItem } from "@plugins/chats/frontend/entities/chat";
import { ChatsBubble } from "./ChatsBubble";

function out(delivery: ChatMessageItem["delivery"], extra: Partial<ChatMessageItem> = {}): ChatMessageItem {
  return { kind: "out", author: "bot", text: "¡Hola!", time: "21:40", delivery, ...extra };
}

describe("doble check de WhatsApp", () => {
  it.each([
    ["sent", "Enviado por WhatsApp 21:45"],
    ["delivered", "Entregado por WhatsApp 21:45"],
    ["read", "Leído por WhatsApp 21:45"],
  ] as const)("%s: el check dice lo que informó WhatsApp y a qué hora", (status, label) => {
    render(<ChatsBubble message={out({ status, time: "21:45" })} />);

    const check = screen.getByRole("img", { name: label });
    expect(check.getAttribute("title")).toBe(label);
    expect(check.dataset.status).toBe(status);
  });

  it("fallido: se ve que no llegó, con el código de Meta, sin pasar el mouse", () => {
    render(
      <ChatsBubble
        message={out({ status: "failed", time: "21:45", errorCode: 131047, errorTitle: "Re-engagement message" })}
      />,
    );

    const failed = screen.getByText("No llegó · 131047");
    expect(failed.getAttribute("title")).toBe(
      "WhatsApp no lo entregó 21:45 · error 131047 (Re-engagement message)",
    );
  });

  it("sin dato de WhatsApp dice «sin confirmación» y no pinta ningún check", () => {
    render(<ChatsBubble message={out({ status: "unconfirmed" })} />);

    expect(screen.getByText("sin confirmación").getAttribute("title")).toBe(
      "WhatsApp todavía no confirmó este mensaje",
    );
    expect(screen.queryByRole("img", { name: /por WhatsApp/ })).toBeNull();
  });

  it("el mensaje del operador también lo lleva", () => {
    render(<ChatsBubble message={out({ status: "read", time: "21:50" }, { author: "human" })} />);

    expect(screen.getByRole("img", { name: "Leído por WhatsApp 21:50" })).toBeTruthy();
  });

  it("la tarjeta del bot (catálogo) y los botones también lo llevan", () => {
    render(
      <>
        <ChatsBubble
          message={out(
            { status: "delivered", time: "21:46" },
            {
              event: {
                kind: "bot_card",
                component: "products_list",
                title: null,
                text: "Para la Noche de Velitas tenemos:",
                items: [],
                link: null,
                images: [],
              },
            },
          )}
        />
        <ChatsBubble
          message={out(
            { status: "unconfirmed" },
            { event: { kind: "bot_buttons", body: "¿Confirmas?", buttons: [{ title: "Sí" }] } },
          )}
        />
      </>,
    );

    expect(screen.getByRole("img", { name: "Entregado por WhatsApp 21:46" })).toBeTruthy();
    expect(screen.getByText("sin confirmación")).toBeTruthy();
  });

  it("los mensajes del cliente no llevan check", () => {
    render(<ChatsBubble message={{ kind: "in", text: "hola", time: "21:39" }} />);

    expect(screen.queryByRole("img", { name: /por WhatsApp/ })).toBeNull();
    expect(screen.queryByText("sin confirmación")).toBeNull();
  });
});
