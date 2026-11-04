/**
 * Si el detalle de la conversación no se puede leer, el panel lo DICE (L-10).
 *
 * Caso 2026-11-04: el parse del detalle falló y el panel quedó en blanco, como
 * si la conversación no tuviera mensajes; el operador no tenía cómo saber que
 * era un error ni cómo reintentar.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ChatsConversation } from "./ChatsConversation";

const refetch = vi.fn();
let messagesState: { data: unknown[]; isError: boolean; refetch: () => void };

vi.mock("@plugins/chats/frontend/entities/chat", () => ({
  useChatInbox: () => ({
    data: [{ id: "wa_570000000001", name: "Cliente", short: "CL", color: "a" }],
  }),
  useChatMessages: () => messagesState,
}));

vi.mock("@plugins/chats/frontend/entities/session", () => ({
  useSession: () => ({ data: undefined }),
}));

vi.mock("./ChatsMessageList", () => ({
  ChatsMessageList: () => <div data-testid="message-list" />,
}));

beforeEach(() => {
  refetch.mockReset();
});

describe("ChatsConversation · error al leer la conversación", () => {
  it("muestra que no se pudo cargar y deja reintentar", () => {
    messagesState = { data: [], isError: true, refetch };

    render(<ChatsConversation chatId="wa_570000000001" />);

    expect(screen.getByRole("alert").textContent).toContain(
      "No se pudo cargar esta conversación",
    );
    expect(screen.queryByTestId("message-list")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("sin error pinta los mensajes como siempre", () => {
    messagesState = { data: [], isError: false, refetch };

    render(<ChatsConversation chatId="wa_570000000001" />);

    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByTestId("message-list")).toBeTruthy();
  });
});
