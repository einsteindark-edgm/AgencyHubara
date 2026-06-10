import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ChatsMessageList } from "./ChatsMessageList";
import type { ChatMessageItem } from "@plugins/chats/frontend/entities/chat";

const scrollToBottomMock = vi.fn();

vi.mock("../model/useAutoScroll", () => ({
  useAutoScroll: vi.fn(() => ({
    containerRef: { current: null },
    sentinelRef: { current: null },
    showNewBadge: false,
    handleScroll: vi.fn(),
    scrollToBottom: scrollToBottomMock,
  })),
}));

// El composer real depende de TanStack Query + entities/handoff y rompe estos
// tests que sólo verifican el list + badge. Lo mockeamos a un stub minimal.
vi.mock("./ChatsComposer", () => ({
  ChatsComposer: () => <div data-testid="composer-mock" />,
}));

import { useAutoScroll } from "../model/useAutoScroll";

const mockUseAutoScroll = vi.mocked(useAutoScroll);

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  scrollToBottomMock.mockClear();
  mockUseAutoScroll.mockReturnValue({
    containerRef: { current: null },
    sentinelRef: { current: null },
    showNewBadge: false,
    handleScroll: vi.fn(),
    scrollToBottom: scrollToBottomMock,
  });
});

const msg = (overrides: Partial<ChatMessageItem> = {}): ChatMessageItem => ({
  kind: "in",
  text: "hola",
  time: "10:00",
  ...overrides,
});

describe("ChatsMessageList", () => {
  it("renders without crash when messages is empty", () => {
    const { container } = render(<ChatsMessageList messages={[]} chatId={null} />);
    expect(container).toBeTruthy();
  });

  it("renders ChatsBubble for each message, no badge initially", () => {
    const messages = [
      msg({ text: "Mensaje 1" }),
      msg({ kind: "out", text: "Mensaje 2" }),
    ];
    render(<ChatsMessageList messages={messages} chatId={null} />);
    expect(screen.getByText("Mensaje 1")).toBeInTheDocument();
    expect(screen.getByText("Mensaje 2")).toBeInTheDocument();
    expect(screen.queryByText("↓ Nuevo mensaje")).not.toBeInTheDocument();
  });

  it("shows badge when useAutoScroll returns showNewBadge=true", () => {
    mockUseAutoScroll.mockReturnValue({
      containerRef: { current: null },
      sentinelRef: { current: null },
      showNewBadge: true,
      handleScroll: vi.fn(),
      scrollToBottom: scrollToBottomMock,
    });
    render(<ChatsMessageList messages={[msg()]} chatId={null} />);
    expect(screen.getByText("↓ Nuevo mensaje")).toBeInTheDocument();
  });

  it("clicking badge calls scrollToBottom", () => {
    mockUseAutoScroll.mockReturnValue({
      containerRef: { current: null },
      sentinelRef: { current: null },
      showNewBadge: true,
      handleScroll: vi.fn(),
      scrollToBottom: scrollToBottomMock,
    });
    render(<ChatsMessageList messages={[msg()]} chatId={null} />);
    fireEvent.click(screen.getByText("↓ Nuevo mensaje"));
    expect(scrollToBottomMock).toHaveBeenCalledOnce();
  });
});
