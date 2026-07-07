import { describe, it, expect } from "vitest";
import {
  isAgentEcho,
  isGhostTrigger,
  isTechnicalEvent,
  isVisibleChatMessage,
  getMessageSender,
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

  it("getMessageSender returns \"user\" for user_message", () => {
    expect(getMessageSender(msg({ ui_type: "user_message" }))).toBe("user");
  });

  it("getMessageSender returns \"agent\" for agent_message", () => {
    expect(getMessageSender(msg({ ui_type: "agent_message" }))).toBe("agent");
  });

  it("getMessageSender returns \"agent\" for unknown ui_type passing filter", () => {
    // system_event would be filtered by isVisibleChatMessage upstream, but
    // getMessageSender itself must still return "agent" for any non-user_message.
    expect(getMessageSender(msg({ ui_type: "system_event" }))).toBe("agent");
  });

  it("getMessageSender returns \"human\" for human_message", () => {
    expect(getMessageSender(msg({ ui_type: "human_message" }))).toBe("human");
  });

  it("ui_component_sent (envío no-textual del bot) es visible en el panel central", () => {
    const note = msg({
      ui_type: "ui_component_sent",
      role: "assistant",
      content: "📋 El bot pidió los datos de envío (formulario)",
    });
    expect(isTechnicalEvent(note)).toBe(false);
    expect(isVisibleChatMessage(note)).toBe(true);
    expect(getMessageSender(note)).toBe("agent");
  });

  it("human_message passes isVisibleChatMessage", () => {
    expect(
      isVisibleChatMessage(
        msg({
          ui_type: "human_message",
          role: "assistant",
          sender: "human",
          content: "Hola, soy del equipo",
        }),
      ),
    ).toBe(true);
  });
});
