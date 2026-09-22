/**
 * Fila de la bandeja: chip del cliente pospuesto («les escribo la otra
 * semana»). Con el filtro "Pospuestos" el operador ve de un vistazo cuándo
 * retoma cada uno y si la cita ya salió — sin abrir chat por chat.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ChatInboxItem } from "@plugins/chats/frontend/entities/chat";

const chats = vi.hoisted(() => ({ current: [] as ChatInboxItem[] }));

vi.mock("@plugins/chats/frontend/entities/chat", async (importOriginal) => ({
  ...(await importOriginal<
    typeof import("@plugins/chats/frontend/entities/chat")
  >()),
  useChatInbox: () => ({ data: chats.current }),
}));

import { ChatsInbox } from "./ChatsInbox";

function makeItem(overrides: Partial<ChatInboxItem> = {}): ChatInboxItem {
  return {
    id: "wa_573001112233",
    name: "573001112233",
    short: "33",
    snippet: "Dijo que escribe la otra semana",
    time: "15:03",
    timestamp: Math.floor(Date.now() / 1000),
    dayIso: "",
    lastInboundMs: null,
    tag: "INTERESADO",
    tagClass: "t-int",
    color: "purple",
    presence: "online",
    unread: 0,
    // Asignado al humano: el filtro por defecto de la bandeja es "Humano".
    human: true,
    order: null,
    ...overrides,
  };
}

describe("chip de pospuesto en la fila", () => {
  it("muestra cuándo retoma y lo que dijo el cliente en el texto accesible", () => {
    chats.current = [
      makeItem({
        postponed: {
          status: "esperando",
          untilMs: Date.UTC(2026, 8, 28, 15),
          label: "Retoma 28 sep",
          description: "retoma el lun 28 sep",
          text: "Si, pero les escribo la otra semana",
        },
      }),
    ];
    render(<ChatsInbox selectedId={null} onSelect={() => {}} />);
    expect(screen.getByText("Retoma 28 sep")).toBeTruthy();
    expect(
      screen.getByLabelText(
        "Pospuesto: retoma el lun 28 sep — «Si, pero les escribo la otra semana»",
      ),
    ).toBeTruthy();
  });

  it("sin pospuesto no hay chip", () => {
    chats.current = [makeItem()];
    render(<ChatsInbox selectedId={null} onSelect={() => {}} />);
    expect(screen.queryByText(/Retoma/)).toBeNull();
  });
});
