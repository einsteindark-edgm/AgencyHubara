/**
 * "Posponer" en el inspector: el operador marca "retomar el <fecha>" (también
 * en chats que tomó un humano) y lo ve/quita desde el mismo panel.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ChatInboxItem } from "@plugins/chats/frontend/entities/chat";
import { ChatsInspector } from "./ChatsInspector";

const inbox = vi.hoisted(() => ({ current: [] as Partial<ChatInboxItem>[] }));
const postponeMock = vi.fn();
const clearMock = vi.fn();

vi.mock("@plugins/chats/frontend/entities/chat", () => ({
  useChatInbox: vi.fn(() => ({ data: inbox.current })),
  useChatMemory: vi.fn(() => ({ data: [] })),
  useChatRoutingLog: vi.fn(() => ({ data: [] })),
  useChatOverview: vi.fn(() => ({ data: null })),
}));

vi.mock("@plugins/chats/frontend/entities/session-tag", () => ({
  OPERATOR_TAGS: ["INTERESADO"],
  OPERATOR_TAG_LABELS: { INTERESADO: "Interesado" },
  useReassignTagMutation: vi.fn(() => ({ mutate: vi.fn(), isPending: false, isError: false, error: null })),
}));

vi.mock("@plugins/chats/frontend/entities/session-postpone", () => ({
  usePostponeMutation: vi.fn(() => ({ mutate: postponeMock, isPending: false, isError: false, error: null })),
  useClearPostponeMutation: vi.fn(() => ({ mutate: clearMock, isPending: false, isError: false, error: null })),
}));

const ID = "wa_573001234567";

beforeEach(() => {
  inbox.current = [{ id: ID, tag: "HUMANO", postponed: null }];
  postponeMock.mockReset();
  clearMock.mockReset();
});

describe("Posponer desde el inspector", () => {
  it("elige la fecha, escribe qué retomar y lo envía", async () => {
    const user = userEvent.setup();
    render(<ChatsInspector chatId={ID} />);
    await user.click(screen.getByText("Posponer"));
    await user.type(screen.getByLabelText("Retomar el"), "2026-09-28");
    await user.type(screen.getByLabelText("Qué hay que retomar"), "Llamar para cerrar el pedido");
    await user.click(screen.getByText("Posponer hasta esa fecha"));
    expect(postponeMock).toHaveBeenCalledTimes(1);
    expect(postponeMock.mock.calls[0][0]).toEqual({
      date: "2026-09-28",
      note: "Llamar para cerrar el pedido",
    });
  });

  it("sin fecha no se puede enviar", async () => {
    const user = userEvent.setup();
    render(<ChatsInspector chatId={ID} />);
    await user.click(screen.getByText("Posponer"));
    expect(screen.getByText("Posponer hasta esa fecha")).toBeDisabled();
  });

  it("si ya está pospuesto muestra hasta cuándo y deja quitarlo", async () => {
    inbox.current = [
      {
        id: ID,
        tag: "HUMANO",
        postponed: {
          status: "vencido",
          untilMs: Date.UTC(2026, 8, 28, 15),
          label: "Retomar 28 sep",
          description: "había que retomar el lun 28 sep",
          text: "Llamar para cerrar el pedido",
          overdue: true,
          manual: true,
        },
      },
    ];
    const user = userEvent.setup();
    render(<ChatsInspector chatId={ID} />);
    expect(screen.getByText(/había que retomar el lun 28 sep/)).toBeTruthy();
    expect(screen.getByText(/Llamar para cerrar el pedido/)).toBeTruthy();
    await user.click(screen.getByText("Quitar pospuesto"));
    expect(clearMock).toHaveBeenCalledTimes(1);
  });
});
