import { describe, it, expect } from "vitest";
import {
  isAgentEcho,
  isGhostTrigger,
  isTechnicalEvent,
  isVisibleChatMessage,
} from "./filters";
import type { ChatMessage } from "./model";

const msg = (overrides: Partial<ChatMessage>): ChatMessage => ({
  ui_type: "user_message",
  role: "user",
  content: "hola",
  ...overrides,
});

describe("message filters", () => {
  it("hides technical events from chat panel", () => {
    expect(isTechnicalEvent(msg({ ui_type: "system_event" }))).toBe(true);
    expect(isTechnicalEvent(msg({ ui_type: "agent_tool_call" }))).toBe(true);
    expect(
      isTechnicalEvent(msg({ ui_type: "tool_execution_result" })),
    ).toBe(true);
    expect(isTechnicalEvent(msg({ ui_type: "user_message" }))).toBe(false);
  });

  it("detects ghost triggers from Temporal injection", () => {
    expect(
      isGhostTrigger(
        msg({ content: "[SISTEMA]: Remarketing acaba de recuperar al cliente" }),
      ),
    ).toBe(true);
    expect(isGhostTrigger(msg({ content: "hola" }))).toBe(false);
  });

  it("detects agent echoes from tool confirmations", () => {
    expect(
      isAgentEcho(
        msg({
          ui_type: "agent_message",
          content: "Esta conversación ha sido etiquetada como INTERESADO",
        }),
      ),
    ).toBe(true);
    expect(
      isAgentEcho(
        msg({ ui_type: "agent_message", content: "respuesta normal" }),
      ),
    ).toBe(false);
  });

  it("isVisibleChatMessage combines all filters", () => {
    expect(isVisibleChatMessage(msg({ content: "hola" }))).toBe(true);
    expect(
      isVisibleChatMessage(msg({ ui_type: "tool_execution_result" })),
    ).toBe(false);
    expect(
      isVisibleChatMessage(msg({ content: "[SISTEMA]: trigger" })),
    ).toBe(false);
  });
});
