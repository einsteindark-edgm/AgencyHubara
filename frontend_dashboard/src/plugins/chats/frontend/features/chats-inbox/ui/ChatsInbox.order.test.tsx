/**
 * Fila de la bandeja: chip del pedido al que pertenece la conversación.
 *
 * Con el filtro "Asignadas al humano" activo conviven chats que ya son un
 * pedido (esperando verificación de pago, coordinando envío…) con chats que
 * siguen en negociación. Sin una marca en la fila el operador tiene que abrir
 * cada uno para saber cuál es cuál.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ChatInboxItem } from "@plugins/chats/frontend/entities/chat";

const chats = vi.hoisted(() => ({ current: [] as ChatInboxItem[] }));

// Sólo el hook de datos es mock; `ORDER_BADGE_META` (los textos del estado de
// pago) es lógica pura de la entity y el chip la usa de verdad.
vi.mock("@plugins/chats/frontend/entities/chat", async (importOriginal) => ({
  ...(await importOriginal<
    typeof import("@plugins/chats/frontend/entities/chat")
  >()),
  useChatInbox: () => ({ data: chats.current }),
}));

import { ChatsInbox } from "./ChatsInbox";

function makeItem(overrides: Partial<ChatInboxItem> = {}): ChatInboxItem {
  return {
    id: "wa_573229041190",
    name: "573229041190",
    short: "90",
    snippet: "Confirmó pedido, falta verificar el pago",
    time: "14:02",
    timestamp: Math.floor(Date.now() / 1000),
    dayIso: "",
    lastInboundMs: null,
    tag: "HUMANO",
    tagClass: "t-human",
    color: "purple",
    presence: "online",
    unread: 0,
    human: true,
    order: null,
    ...overrides,
  };
}

function renderInbox(items: ChatInboxItem[]) {
  chats.current = items;
  render(<ChatsInbox selectedId={null} onSelect={() => {}} />);
}

describe("chip de pedido en la fila", () => {
  it("muestra el número del pedido cuando la conversación ya es una orden", () => {
    renderInbox([
      makeItem({
        order: { label: "#31", orderId: "order_01K", payment: "pending", count: 1 },
      }),
    ]);
    expect(screen.getByText("#31")).toBeTruthy();
  });

  it("el estado del pago no viaja solo en el color — va en el texto accesible", () => {
    renderInbox([
      makeItem({
        order: { label: "#28", orderId: "order_01K", payment: "confirmed", count: 1 },
      }),
    ]);
    expect(screen.getByLabelText("Pedido #28, pago confirmado")).toBeTruthy();
  });

  it("varios pedidos del mismo cliente: el más reciente + cuántos más", () => {
    renderInbox([
      makeItem({
        order: { label: "#33", orderId: "order_01K", payment: "pending", count: 2 },
      }),
    ]);
    expect(screen.getByText("#33 +1")).toBeTruthy();
  });

  it("conversación sin pedido: ningún chip", () => {
    renderInbox([makeItem({ order: null })]);
    expect(screen.queryByLabelText(/^Pedido/)).toBeNull();
  });
});
