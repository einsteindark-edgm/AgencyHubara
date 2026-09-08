/**
 * D1.4 (MBA): el backend persiste los ecos de Meta Business Agent como turnos
 * `assistant` con `sender: "mba"`. El contrato tiene que aceptarlos: un
 * `parse` que falle tumba la sesión entera en el boundary de `useSession`.
 */
import { describe, expect, it } from "vitest";
import { chatMessageSchema } from "./contracts";

describe("chatMessageSchema · sender", () => {
  it("accepts a Meta Business Agent echo (sender = mba) as an agent message", () => {
    const parsed = chatMessageSchema.parse({
      ui_type: "agent_message",
      role: "assistant",
      content: "Hola 🤍, ¿qué aroma buscas?",
      sender: "mba",
      wamid: "wamid.STANDBY.ECHO.1",
      timestamp: "2026-09-08T10:00:00+00:00",
    });
    expect(parsed.sender).toBe("mba");
  });

  it("still accepts human and bot turns", () => {
    expect(chatMessageSchema.parse({ ui_type: "human_message", role: "assistant", content: "x", sender: "human" }).sender).toBe("human");
    expect(chatMessageSchema.parse({ ui_type: "agent_message", role: "assistant", content: "x" }).sender).toBeUndefined();
  });

  it("rejects an unknown sender", () => {
    expect(() => chatMessageSchema.parse({ ui_type: "agent_message", role: "assistant", content: "x", sender: "robot" })).toThrow();
  });
});
