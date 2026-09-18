/**
 * Buscador de la bandeja de Chats.
 *
 * Bug reportado (2026-09-18): "Buscar conversaciones…" era un `<input>` sin
 * estado — escribir no filtraba nada. Contrato: lo escrito acota la bandeja
 * (lista, pills y banner cuentan sobre lo mismo) y una búsqueda sin resultados
 * lo dice, en vez de afirmar "Todo bajo control" con la cola llena.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ChatInboxItem } from "@plugins/chats/frontend/entities/chat";

const chats = vi.hoisted(() => ({ current: [] as ChatInboxItem[] }));

vi.mock("@plugins/chats/frontend/entities/chat", async (importOriginal) => ({
  ...(await importOriginal<
    typeof import("@plugins/chats/frontend/entities/chat")
  >()),
  useChatInbox: () => ({ data: chats.current }),
}));

import { ChatsInbox } from "./ChatsInbox";

function makeItem(overrides: Partial<ChatInboxItem> & { id: string }): ChatInboxItem {
  return {
    name: "573001234567",
    short: "67",
    snippet: "…",
    time: "14:02",
    timestamp: 1_000,
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

// Dos en la cola del humano (el filtro por defecto) y una que no.
const KIT = makeItem({
  id: "kit",
  name: "573001234567",
  snippet: "Quiere un kit de cumpleaños",
  order: { label: "#31", orderId: "order_01K", payment: "pending", count: 1 },
});
const ENVIO = makeItem({
  id: "envio",
  name: "573001112233",
  snippet: "Pregunta por el envío a Cali",
});
const PORTAVELAS = makeItem({
  id: "portavelas",
  name: "573004445566",
  snippet: "Interesada en portavelas",
  tag: "INTERESADO",
  tagClass: "t-int",
  human: false,
});

function renderInbox() {
  chats.current = [KIT, ENVIO, PORTAVELAS];
  render(<ChatsInbox selectedId={null} onSelect={() => {}} />);
  return userEvent.setup();
}

const searchBox = () => screen.getByPlaceholderText("Buscar conversaciones…");

/** Contador del pill (`Interesado 1` → 1). */
function pillCount(label: string): number {
  const pill = screen.getByRole("button", { name: new RegExp(`^${label}\\b`) });
  return Number(pill.querySelector(".ct")?.textContent);
}

describe("buscador de la bandeja", () => {
  it("escribir deja solo las conversaciones que coinciden (sin importar tildes)", async () => {
    const user = renderInbox();
    await user.type(searchBox(), "envio");

    expect(screen.getByText("Pregunta por el envío a Cali")).toBeTruthy();
    expect(screen.queryByText("Quiere un kit de cumpleaños")).toBeNull();
  });

  it("encuentra por teléfono aunque se escriba con espacios", async () => {
    const user = renderInbox();
    await user.type(searchBox(), "300 111 2233");

    expect(screen.getByText("Pregunta por el envío a Cali")).toBeTruthy();
    expect(screen.queryByText("Quiere un kit de cumpleaños")).toBeNull();
  });

  it("encuentra por número de pedido", async () => {
    const user = renderInbox();
    await user.type(searchBox(), "#31");

    expect(screen.getByText("Quiere un kit de cumpleaños")).toBeTruthy();
    expect(screen.queryByText("Pregunta por el envío a Cali")).toBeNull();
  });

  it("pills y banner cuentan solo lo que coincide con la búsqueda", async () => {
    const user = renderInbox();
    await user.type(searchBox(), "portavelas");

    expect(screen.getByText("0 conversaciones esperando respuesta")).toBeTruthy();
    expect(pillCount("Todas")).toBe(1);
    expect(pillCount("Interesado")).toBe(1);
  });

  it("sin resultados en la cola del humano no dice 'Todo bajo control': ofrece verlos en Todas", async () => {
    const user = renderInbox();
    await user.type(searchBox(), "portavelas");

    expect(screen.queryByText("Todo bajo control")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Ver en Todas" }));
    expect(screen.getByText("Interesada en portavelas")).toBeTruthy();
  });

  it("sin resultados en ninguna vista lo dice y deja limpiar la búsqueda", async () => {
    const user = renderInbox();
    await user.type(searchBox(), "zzz");

    expect(screen.getByText("Sin resultados para «zzz»")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Limpiar búsqueda" }));
    expect(searchBox()).toHaveValue("");
    expect(screen.getByText("Quiere un kit de cumpleaños")).toBeTruthy();
    expect(screen.getByText("Pregunta por el envío a Cali")).toBeTruthy();
  });
});
