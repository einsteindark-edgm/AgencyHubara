/**
 * Costo de Jev en Ads (2026-10-01): lo que cobra el clasificador del motor de
 * decisiones (OpenRouter) por las preguntas de cada conversación, como el
 * costo LLM y el de WhatsApp. `jev_cost_usd_micros` (entero, 1e-6 USD) +
 * `jev_calls` (preguntas). Un backend viejo no manda nada → null, nunca rompe.
 */
import { describe, expect, it } from "vitest";

import { mapBackendCampaign, mapBackendConversation } from "./api";
import { backendAdsCampaignsResponseSchema, backendAttributedConversationSchema } from "./contracts";

const conversation = {
  id: "wa_573001234567__ep_001",
  phone_number: "573001234567",
  episode_id: "ep_001",
  started_at_ms: 1779800400000,
  last_msg_at_ms: null,
  msgs_count: 8,
  ad_headline: "Velas",
  agent: "ventas",
  state: "calificado",
  name: null,
  city: null,
  value: null,
  duration_ms: null,
  llm_cost_usd: null,
  llm_tokens: null,
  capi_event: null,
};

describe("costo de Jev", () => {
  it("la conversación trae lo que costó Jev y cuántas preguntas", () => {
    const row = mapBackendConversation(
      backendAttributedConversationSchema.parse({ ...conversation, jev_cost_usd_micros: 105, jev_calls: 5 }),
    );
    expect(row.jevCostUsdMicros).toBe(105);
    expect(row.jevCalls).toBe(5);
  });

  it("un backend viejo no lo manda: null (≠ costó 0)", () => {
    const row = mapBackendConversation(backendAttributedConversationSchema.parse(conversation));
    expect(row.jevCostUsdMicros).toBeNull();
    expect(row.jevCalls).toBeNull();
  });

  it("la campaña suma el costo de Jev de sus conversaciones", () => {
    const parsed = backendAdsCampaignsResponseSchema.parse({
      campaigns: [
        {
          id: "CAMP_9", name: null, source_type: "ad", started: 2, first_seen_ms: null, last_seen_ms: null,
          conversations: null, revenue: null, avg_ticket: null, llm_cost_usd: null, llm_tokens: null,
          avg_episode_duration_ms: null, spend: null, impressions: null, reach: null, clicks: null, status: null,
          objective: null, placement: null, audience: null, ad_set: null, creative_title: null, template: null,
          meta_campaign_id: null, first_resp: null, tendency: null, days_run: null,
          jev_cost_usd_micros: 400, jev_calls: 20,
        },
      ],
    });
    const camp = mapBackendCampaign(parsed.campaigns[0]);
    expect(camp.jevCostUsdMicros).toBe(400);
    expect(camp.jevCalls).toBe(20);
  });
  it("la conversación trae lo que costó leer sus fotos (describir, huella y comparar)", () => {
    const row = mapBackendConversation(
      backendAttributedConversationSchema.parse({ ...conversation, vision_cost_usd_micros: 940, vision_calls: 3 }),
    );
    expect(row.visionCostUsdMicros).toBe(940);
    expect(row.visionCalls).toBe(3);
    const old = mapBackendConversation(backendAttributedConversationSchema.parse(conversation));
    expect(old.visionCostUsdMicros).toBeNull();
  });
  it("la conversación trae lo que costó transcribir sus notas de voz", () => {
    const row = mapBackendConversation(
      backendAttributedConversationSchema.parse({ ...conversation, audio_cost_usd_micros: 512, audio_calls: 2 }),
    );
    expect(row.audioCostUsdMicros).toBe(512);
    expect(row.audioCalls).toBe(2);
    const old = mapBackendConversation(backendAttributedConversationSchema.parse(conversation));
    expect(old.audioCostUsdMicros).toBeNull();
  });
});
