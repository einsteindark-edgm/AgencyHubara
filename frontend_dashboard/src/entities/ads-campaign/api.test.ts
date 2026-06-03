/**
 * Tests del costo LLM por episodio en la entity ads-campaign:
 *  - `mapBackendConversation` mapea `llm_cost_usd`/`llm_tokens` (snake) →
 *    `llmCostUsd`/`llmTokens` (camel) del dominio.
 *  - el Zod schema valida los campos nuevos (defensa contra contract drift).
 */

import { describe, expect, it } from "vitest";

import { mapBackendConversation } from "./api";
import {
  backendAttributedConversationSchema,
  type BackendAttributedConversation,
} from "./contracts";

const sample: BackendAttributedConversation = {
  id: "wa_57300__ep_001",
  phone_number: "57300",
  episode_id: "ep_001",
  started_at_ms: 1779800400000,
  last_msg_at_ms: null,
  msgs_count: 8,
  ad_headline: "Velas",
  agent: "Sofía",
  state: "ganado",
  name: null,
  city: null,
  value: 124500,
  llm_cost_usd: 0.0042,
  llm_tokens: 1930,
};

describe("mapBackendConversation — costo LLM", () => {
  it("mapea llm_cost_usd/llm_tokens (snake) → llmCostUsd/llmTokens (camel)", () => {
    const c = mapBackendConversation(sample);
    expect(c.llmCostUsd).toBe(0.0042);
    expect(c.llmTokens).toBe(1930);
  });

  it("conserva null cuando el episodio aún no acumuló uso LLM", () => {
    const c = mapBackendConversation({
      ...sample,
      llm_cost_usd: null,
      llm_tokens: null,
    });
    expect(c.llmCostUsd).toBeNull();
    expect(c.llmTokens).toBeNull();
  });
});

describe("backendAttributedConversationSchema — costo LLM", () => {
  it("parsea llm_cost_usd + llm_tokens", () => {
    expect(() =>
      backendAttributedConversationSchema.parse(sample),
    ).not.toThrow();
  });

  it("rechaza un llm_cost_usd string (contract drift)", () => {
    expect(() =>
      backendAttributedConversationSchema.parse({
        ...sample,
        llm_cost_usd: "0.0042",
      }),
    ).toThrow();
  });
});
