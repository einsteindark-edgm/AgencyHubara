/**
 * Contrato Zod de la audiencia de campaña — fixtures con el shape REAL del
 * backend (`GET /api/marketing/campaigns/{id}/audience` +
 * `GET /api/marketing/audience/{session_id}/conversation`).
 *
 * Tolerante (`.default()`): un backend viejo sin campos opcionales NO tumba
 * el dashboard; un shape roto (sin `session_id`) SÍ truena temprano.
 */
import { describe, expect, it } from "vitest";

import {
  backendAudienceConversationSchema,
  backendCampaignAudienceSchema,
} from "./contracts";

/** Shape EXACTO de GET /campaigns/{id}/audience. */
const audienceFixture = {
  recipients: [
    {
      session_id: "wa_573001234567",
      phone: "+573001234567",
      customer_name: "Camila",
      segment: "clientes",
    },
    {
      session_id: "wa_573007654321",
      phone: "+573007654321",
      customer_name: null,
      segment: "interesados",
    },
  ],
  skipped: [
    { session_id: "wa_573000000001", phone: "+573000000001", reason: "excluido" },
    {
      session_id: "wa_573000000002",
      phone: "+573000000002",
      reason: "campana_reciente",
    },
  ],
  total: 2,
};

/** Shape EXACTO de GET /audience/{session_id}/conversation. */
const conversationFixture = {
  session_id: "wa_573001234567",
  messages: [
    {
      role: "user",
      kind: "text",
      content: "Hola, ¿tienen velas aromáticas?",
      timestamp: "2026-07-16T14:30:00Z",
    },
    {
      role: "assistant",
      kind: "text",
      content: "¡Claro! Te muestro el catálogo.",
      timestamp: "2026-07-16T14:30:40Z",
    },
    {
      role: "assistant",
      kind: "template",
      content: "¡Hola Camila! 👋 Tenemos 20% off…",
      timestamp: null,
    },
  ],
};

describe("backendCampaignAudienceSchema", () => {
  it("parsea la audiencia real: recipients + skipped + total", () => {
    const parsed = backendCampaignAudienceSchema.parse(audienceFixture);
    expect(parsed.recipients).toHaveLength(2);
    expect(parsed.recipients[0]?.customer_name).toBe("Camila");
    expect(parsed.recipients[1]?.customer_name).toBeNull();
    expect(parsed.skipped[1]?.reason).toBe("campana_reciente");
    expect(parsed.total).toBe(2);
  });

  it("tolera un backend viejo sin campos opcionales (defaults)", () => {
    const parsed = backendCampaignAudienceSchema.parse({
      recipients: [{ session_id: "wa_573001" }],
      skipped: [{ session_id: "wa_573002" }],
    });
    expect(parsed.recipients[0]?.phone).toBe("");
    expect(parsed.recipients[0]?.customer_name).toBeNull();
    expect(parsed.recipients[0]?.segment).toBe("");
    expect(parsed.skipped[0]?.reason).toBe("");
    expect(parsed.total).toBe(0);
  });

  it("rechaza un recipient sin session_id (drift visible, no silencioso)", () => {
    expect(() =>
      backendCampaignAudienceSchema.parse({
        recipients: [{ phone: "+573001234567" }],
        skipped: [],
        total: 1,
      }),
    ).toThrow();
  });
});

describe("backendAudienceConversationSchema", () => {
  it("parsea la conversación real: roles, kinds y timestamps nullables", () => {
    const parsed = backendAudienceConversationSchema.parse(conversationFixture);
    expect(parsed.session_id).toBe("wa_573001234567");
    expect(parsed.messages).toHaveLength(3);
    expect(parsed.messages[0]?.role).toBe("user");
    expect(parsed.messages[2]?.kind).toBe("template");
    expect(parsed.messages[2]?.timestamp).toBeNull();
  });

  it("tolera mensajes sin campos opcionales (defaults)", () => {
    const parsed = backendAudienceConversationSchema.parse({
      session_id: "wa_573001",
      messages: [{}],
    });
    expect(parsed.messages[0]?.role).toBe("assistant");
    expect(parsed.messages[0]?.kind).toBe("text");
    expect(parsed.messages[0]?.content).toBe("");
    expect(parsed.messages[0]?.timestamp).toBeNull();
  });

  it("rechaza una respuesta sin session_id", () => {
    expect(() =>
      backendAudienceConversationSchema.parse({ messages: [] }),
    ).toThrow();
  });
});
