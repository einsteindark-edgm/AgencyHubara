/**
 * El detalle de una conversación nunca se cae entero por UN mensaje (L-10).
 *
 * Caso 2026-11-04: la invitación a calificar (#428) entró al historial con
 * `sender: "reviews"`; el contrato no lo conocía, `parse` lanzó y el panel de
 * chat quedó en blanco en todas las conversaciones que la recibieron, aunque
 * la respuesta del servidor traía los mensajes completos.
 */
import { describe, expect, it } from "vitest";
import { sessionDetailsSchema } from "./contracts";

const detail = (messages: unknown[]) => ({
  session_id: "wa_570000000001",
  phone_number: "570000000001",
  tag: "HUMANO",
  motivo: "Intervención desde la app",
  memory_content: null,
  active_agent_route: "humano",
  phone_number_id: "100000000000001",
  status_history: [],
  messages,
});

const userMessage = {
  ui_type: "user_message",
  role: "user",
  content: "Gracias",
  timestamp: "2026-10-29T15:17:39+00:00",
};

const reviewInvitation = {
  ui_type: "agent_message",
  role: "assistant",
  kind: "notice",
  sender: "reviews",
  content: "¡Tu pedido #900 ya llegó! 🕯️\n[Calificar mi pedido]",
  timestamp: "2026-10-29T18:37:13+00:00",
  wamid: "wamid.REVIEW.1",
  delivery: { status: "read", at_ms: 1793299179000, error_code: null, error_title: null },
};

describe("sessionDetailsSchema · mensajes", () => {
  it("parsea una conversación con la invitación a calificar y conserva todos sus mensajes", () => {
    const parsed = sessionDetailsSchema.parse(detail([userMessage, reviewInvitation]));

    expect(parsed.messages.map((m) => m.content)).toEqual([
      "Gracias",
      "¡Tu pedido #900 ya llegó! 🕯️\n[Calificar mi pedido]",
    ]);
  });

  it("un mensaje que el panel no sabe leer se ve como nota y el resto de la conversación sigue", () => {
    const unreadable = {
      ui_type: "mensaje_del_futuro",
      role: "assistant",
      content: "texto que el cliente sí recibió",
      timestamp: "2026-10-29T16:00:00+00:00",
    };

    const parsed = sessionDetailsSchema.parse(
      detail([userMessage, unreadable, reviewInvitation]),
    );

    expect(parsed.messages).toHaveLength(3);
    const [, note] = parsed.messages;
    // Visible en el panel (nota de sistema), en su lugar del día, con el texto
    // que traía: el operador ve que algo se dijo ahí, no un hueco.
    expect(note.ui_type).toBe("ui_component_sent");
    expect(note.timestamp).toBe("2026-10-29T16:00:00+00:00");
    expect(note.content).toContain("no se pudo mostrar");
    expect(note.content).toContain("texto que el cliente sí recibió");
  });

  it("un mensaje sin nada legible queda como nota sin texto inventado", () => {
    const parsed = sessionDetailsSchema.parse(detail([42, userMessage]));

    expect(parsed.messages).toHaveLength(2);
    expect(parsed.messages[0].ui_type).toBe("ui_component_sent");
    expect(parsed.messages[0].timestamp).toBeUndefined();
    expect(parsed.messages[1].content).toBe("Gracias");
  });
});
