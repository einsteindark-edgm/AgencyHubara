/**
 * Conversaciones que llegaron desde un agente de IA (ChatGPT, Gemini…).
 *
 * El backend (`GET /api/ads/agent-referrals`) cuenta `metadata.agent_referrals`
 * — el `via:` que la tienda pone en el botón de WhatsApp. Acá: el contrato Zod
 * (defensa contra drift) y el mapeo a dominio con etiquetas legibles.
 */

import { describe, expect, it } from "vitest";

import { mapAgentReferrals } from "./api";

const raw = {
  total: 3,
  with_order: 1,
  by_source: { chatgpt: 2, gemini: 1, perplexity: 0, copilot: 0, claude: 0 },
};

describe("mapAgentReferrals", () => {
  it("mapea totales y pone nombre de marca a cada agente", () => {
    const out = mapAgentReferrals(raw);

    expect(out.total).toBe(3);
    expect(out.withOrder).toBe(1);
    expect(out.bySource).toEqual([
      { source: "chatgpt", label: "ChatGPT", count: 2 },
      { source: "gemini", label: "Gemini", count: 1 },
      { source: "perplexity", label: "Perplexity", count: 0 },
      { source: "copilot", label: "Copilot", count: 0 },
      { source: "claude", label: "Claude", count: 0 },
    ]);
  });

  it("rechaza una respuesta con forma distinta", () => {
    expect(() => mapAgentReferrals({ total: "3" })).toThrow();
  });
});
