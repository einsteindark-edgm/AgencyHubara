/**
 * Tests del costo LLM por episodio en la entity ads-campaign:
 *  - `mapBackendConversation` mapea `llm_cost_usd`/`llm_tokens` (snake) →
 *    `llmCostUsd`/`llmTokens` (camel) del dominio.
 *  - el Zod schema valida los campos nuevos (defensa contra contract drift).
 */

import { describe, expect, it } from "vitest";

import { mapBackendCampaign, mapBackendConversation } from "./api";
import {
  backendAdsCampaignSchema,
  backendAdsDailyResponseSchema,
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
  duration_ms: 4200000,
  llm_cost_usd: 0.0042,
  llm_tokens: 1930,
  capi_event: null,
};

/** Campaña backend mínima válida — todos los nullable en null. Sin los campos
 *  CAPI a propósito: el schema debe defaultearlos a 0 (backend viejo). */
const campaignSample = {
  id: "AD_X",
  name: "Velas artesanales",
  source_type: "ad",
  started: 12,
  first_seen_ms: 1779800400000,
  last_seen_ms: 1779900400000,
  conversations: null,
  revenue: null,
  avg_ticket: null,
  llm_cost_usd: null,
  llm_tokens: null,
  avg_episode_duration_ms: null,
  spend: null,
  impressions: null,
  reach: null,
  clicks: null,
  status: null,
  objective: null,
  placement: null,
  audience: null,
  ad_set: null,
  creative_title: null,
  template: null,
  meta_campaign_id: null,
  first_resp: null,
  tendency: null,
  days_run: null,
};

describe("mapBackendCampaign — sourceType (campañas internas de marketing)", () => {
  it("mapea source_type al dominio — distingue hubara_campaign de Meta", () => {
    const meta = mapBackendCampaign(campaignSample as never);
    expect(meta.sourceType).toBe("ad");
    const interna = mapBackendCampaign({
      ...campaignSample,
      id: "mkt-abc",
      source_type: "hubara_campaign",
    } as never);
    expect(interna.sourceType).toBe("hubara_campaign");
  });
});

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

describe("mapBackendConversation — value + duración", () => {
  it("mapea value (COP) y duration_ms → durationMs", () => {
    const c = mapBackendConversation(sample);
    expect(c.value).toBe(124500);
    expect(c.durationMs).toBe(4200000);
  });

  it("conserva null en value/durationMs cuando el episodio no cerró venta", () => {
    const c = mapBackendConversation({
      ...sample,
      value: null,
      duration_ms: null,
    });
    expect(c.value).toBeNull();
    expect(c.durationMs).toBeNull();
  });
});

describe("backendAdsCampaignSchema — counters CAPI", () => {
  it("defaultea los counters a 0 cuando el backend aún no los serializa", () => {
    const parsed = backendAdsCampaignSchema.parse(campaignSample);
    expect(parsed.capi_leads_sent).toBe(0);
    expect(parsed.capi_purchases_sent).toBe(0);
    expect(parsed.capi_failed).toBe(0);
  });

  it("parsea los counters cuando vienen poblados", () => {
    const parsed = backendAdsCampaignSchema.parse({
      ...campaignSample,
      capi_leads_sent: 3,
      capi_purchases_sent: 1,
      capi_failed: 2,
    });
    expect(parsed.capi_leads_sent).toBe(3);
    expect(parsed.capi_purchases_sent).toBe(1);
    expect(parsed.capi_failed).toBe(2);
  });

  it("rechaza un counter string (contract drift)", () => {
    expect(() =>
      backendAdsCampaignSchema.parse({
        ...campaignSample,
        capi_leads_sent: "3",
      }),
    ).toThrow();
  });
});

describe("mapBackendCampaign — counters CAPI", () => {
  it("mapea capi_* (snake) → capi* (camel) del dominio", () => {
    const c = mapBackendCampaign(
      backendAdsCampaignSchema.parse({
        ...campaignSample,
        capi_leads_sent: 3,
        capi_purchases_sent: 1,
        capi_failed: 2,
      }),
    );
    expect(c.capiLeadsSent).toBe(3);
    expect(c.capiPurchasesSent).toBe(1);
    expect(c.capiFailed).toBe(2);
  });
});

describe("capi_event — schema + mapper", () => {
  it("defaultea capi_event a null si el backend aún no lo serializa", () => {
    const { capi_event: _omit, ...rest } = sample;
    void _omit;
    expect(backendAttributedConversationSchema.parse(rest).capi_event).toBeNull();
  });

  it("mapea capi_event → capiEvent ('Purchase' | 'LeadSubmitted')", () => {
    expect(
      mapBackendConversation({ ...sample, capi_event: "Purchase" }).capiEvent,
    ).toBe("Purchase");
    expect(
      mapBackendConversation({ ...sample, capi_event: "LeadSubmitted" })
        .capiEvent,
    ).toBe("LeadSubmitted");
  });

  it("null y valores desconocidos → capiEvent null (narrowing defensivo)", () => {
    expect(
      mapBackendConversation({ ...sample, capi_event: null }).capiEvent,
    ).toBeNull();
    expect(
      mapBackendConversation({ ...sample, capi_event: "Lead" }).capiEvent,
    ).toBeNull();
  });
});

describe("segmentación — meta_adset_id + response de /adsets", () => {
  it("defaultea meta_adset_id a null si el backend aún no lo serializa", () => {
    const parsed = backendAdsCampaignSchema.parse(campaignSample);
    expect(parsed.meta_adset_id).toBeNull();
  });

  it("mapea meta_adset_id → metaAdsetId del dominio", () => {
    const c = mapBackendCampaign(
      backendAdsCampaignSchema.parse({
        ...campaignSample,
        ad_set: "Hombres 25-45 Bogotá",
        meta_adset_id: "ADSET_3",
      }),
    );
    expect(c.metaAdsetId).toBe("ADSET_3");
    expect(c.adSet).toBe("Hombres 25-45 Bogotá");
  });

  it("parsea el response de GET /campaigns/{id}/adsets (mismo shape de campaña por fila)", async () => {
    const { backendAdsAdsetsResponseSchema } = await import("./contracts");
    const parsed = backendAdsAdsetsResponseSchema.parse({
      campaign_id: "CAMP_9",
      ad_sets: [
        {
          ...campaignSample,
          id: "ADSET_3",
          name: "Hombres 25-45 Bogotá",
          ad_set: "Hombres 25-45 Bogotá",
          meta_adset_id: "ADSET_3",
          meta_campaign_id: "CAMP_9",
          spend: 320500,
        },
      ],
    });
    expect(parsed.ad_sets[0].meta_adset_id).toBe("ADSET_3");
    expect(parsed.ad_sets[0].spend).toBe(320500);
  });
});

describe("backendAdsDailyResponseSchema — serie diaria", () => {
  const okPoint = {
    d: "21 may",
    ganado: 2,
    cotizado: 3,
    calificado: 4,
    activo: 5,
    nuevo: 2,
    no_reply: 8,
    perdido: 1,
  };

  it("parsea una serie diaria válida", () => {
    expect(() =>
      backendAdsDailyResponseSchema.parse({
        campaign_id: "AD_X",
        days: 14,
        series: [okPoint],
      }),
    ).not.toThrow();
  });

  it("rechaza un count no-entero (contract drift)", () => {
    expect(() =>
      backendAdsDailyResponseSchema.parse({
        campaign_id: "AD_X",
        days: 14,
        series: [{ ...okPoint, ganado: "2" }],
      }),
    ).toThrow();
  });
});
