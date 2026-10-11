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

  it("un turn_key raro no tumba la sesión: la burbuja queda sin botón de turno (L-10)", () => {
    const base = { ui_type: "agent_message", role: "assistant", content: "x" };

    expect(chatMessageSchema.parse({ ...base, turn_key: null }).turn_key).toBeUndefined();
    expect(chatMessageSchema.parse({ ...base, turn_key: 7 }).turn_key).toBeUndefined();
    expect(chatMessageSchema.parse({ ...base, turn_key: "run:x/t:2" }).turn_key).toBe("run:x/t:2");
  });

  it("rejects an unknown sender", () => {
    expect(() => chatMessageSchema.parse({ ui_type: "agent_message", role: "assistant", content: "x", sender: "robot" })).toThrow();
  });

});

describe("chatMessageSchema · mensaje que Meta entregó tarde", () => {
  const base = { ui_type: "user_message", role: "user", content: "hola, ¿siguen teniendo velas?" };

  it("conserva cuándo lo escribió el cliente y que llegó con la ventana cerrada", () => {
    const parsed = chatMessageSchema.parse({
      ...base,
      timestamp: "2026-10-09T21:35:42+00:00",
      sent_at: "2026-10-06T17:02:04+00:00",
      arrived_after_window: true,
    });
    expect(parsed.sent_at).toBe("2026-10-06T17:02:04+00:00");
    expect(parsed.arrived_after_window).toBe(true);
  });

  it("un valor raro no tumba la sesión: la burbuja queda como un mensaje normal (L-10)", () => {
    const parsed = chatMessageSchema.parse({ ...base, sent_at: { x: 1 }, arrived_after_window: "sí" });
    expect(parsed.sent_at).toBeUndefined();
    expect(parsed.arrived_after_window).toBeUndefined();
  });
});
