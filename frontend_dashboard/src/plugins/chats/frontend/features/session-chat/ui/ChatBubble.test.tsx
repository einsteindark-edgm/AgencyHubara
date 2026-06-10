import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { ChatBubble } from "./ChatBubble";
import type { ChatMessage } from "@plugins/chats/frontend/entities/message";

const userMsg: ChatMessage = {
  ui_type: "user_message",
  role: "user",
  content: "hola",
};

const agentMsg: ChatMessage = {
  ui_type: "agent_message",
  role: "assistant",
  content: "hola desde el agente",
};

describe("ChatBubble", () => {
  it("renders with bubble-user class when sender is \"user\"", () => {
    const { container } = render(<ChatBubble message={userMsg} sender="user" />);
    const bubble = container.firstChild as HTMLElement;
    expect(bubble.classList.contains("bubble-user")).toBe(true);
    expect(bubble.classList.contains("bubble-agent")).toBe(false);
  });

  it("renders with bubble-agent class when sender is \"agent\"", () => {
    const { container } = render(
      <ChatBubble message={agentMsg} sender="agent" />,
    );
    const bubble = container.firstChild as HTMLElement;
    expect(bubble.classList.contains("bubble-agent")).toBe(true);
    expect(bubble.classList.contains("bubble-user")).toBe(false);
  });

  it("renders micro-markdown bold (**x** → <strong>x</strong>)", () => {
    const msg: ChatMessage = {
      ui_type: "agent_message",
      role: "assistant",
      content: "texto **negrita** aquí",
    };
    const { container } = render(<ChatBubble message={msg} sender="agent" />);
    const strong = container.querySelector("strong");
    expect(strong).not.toBeNull();
    expect(strong?.textContent).toBe("negrita");
  });
});
