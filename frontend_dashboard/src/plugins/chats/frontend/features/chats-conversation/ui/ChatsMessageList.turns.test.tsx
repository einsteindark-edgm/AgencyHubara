import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { ChatMessageItem } from "@plugins/chats/frontend/entities/chat";

import { ChatsMessageList } from "./ChatsMessageList";

/**
 * Chats: un botón por turno del bot que abre su hilo (plan del laboratorio
 * PR 17, diseño §11): el mismo diagrama de secuencia del Laboratorio, con
 * lo que pasó de verdad en producción. Visible siempre (también en la app
 * Android, donde no hay hover).
 */

vi.mock("../model/useAutoScroll", () => ({
  useAutoScroll: vi.fn(() => ({
    containerRef: { current: null },
    sentinelRef: { current: null },
    showNewBadge: false,
    handleScroll: vi.fn(),
    scrollToBottom: vi.fn(),
  })),
}));

vi.mock("./ChatsComposer", () => ({
  ChatsComposer: () => <div data-testid="composer-mock" />,
}));

const SID = "wa_573001234567";
const fetchMock = vi.fn();

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
}

const messages: ChatMessageItem[] = [
  { kind: "in", text: "hola", time: "07:00", turnKey: "run:x/t:1" },
  { kind: "out", author: "bot", text: "¡Hola!", time: "07:00", turnKey: "run:x/t:1" },
  { kind: "in", text: "catálogo", time: "07:01", turnKey: "run:x/t:2" },
  { kind: "out", author: "bot", text: "Te dejo el catálogo", time: "07:01", turnKey: "run:x/t:2" },
  { kind: "out", author: "human", text: "te escribo yo", time: "07:02" },
];

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation((url: string) =>
    String(url).includes(`/api/chats/sessions/${SID}/turns/trace?turn_key=run%3Ax%2Ft%3A2`)
      ? json({
          fidelity: "v2",
          trace: { turn: 2, turn_started_ms: 1790200070000, mode: "shadow", sent_texts: ["Te dejo el catálogo"] },
          steps: [
            { i: 0, at_ms: 0, kind: "inbound", messages: [{ seq: 1, text: "catálogo" }] },
            { i: 1, at_ms: 40, kind: "llm", round: 1, dur_ms: 900 },
            { i: 2, at_ms: 950, kind: "outbound", bubbles: [{ kind: "text", text: "Te dejo el catálogo" }] },
          ],
        })
      : json({ detail: "no" }, 404),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ChatsMessageList messages={messages} chatId={SID} />
    </QueryClientProvider>,
  );
}

describe("ChatsMessageList — hilo de cada turno", () => {
  it("pone UN botón por turno del bot, al final del turno; el operador humano no tiene", () => {
    renderList();

    const buttons = screen.getAllByRole("button", { name: /Ver el hilo del turno/ });
    expect(buttons).toHaveLength(2);
  });

  it("el botón abre el hilo del turno con su diagrama", async () => {
    renderList();

    fireEvent.click(screen.getAllByRole("button", { name: /Ver el hilo del turno/ })[1]);

    const dialog = await screen.findByRole("dialog", { name: /Hilo del turno 2/ });
    expect(within(dialog).getByText(/bot nuevo · sombra/)).toBeInTheDocument();
    expect(await within(dialog).findByRole("group", { name: /secuencia/i })).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cerrar el hilo" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
