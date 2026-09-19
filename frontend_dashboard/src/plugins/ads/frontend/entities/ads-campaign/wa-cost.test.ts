/**
 * Costos de WhatsApp en Ads (2026-09-18): contrato + mapper + desglose.
 *
 * Los fixtures son el shape REAL que emite el backend
 * (`hubara_agency/tests/plugins/ads/test_wa_cost_endpoint.py` lo verifica por
 * HTTP): `wa_cost_usd_micros` (entero, USD micros = 1e-6 USD),
 * `wa_cost_by_category` = {categoría Meta: {count, usd_micros}} y
 * `wa_msgs_pending`. Un backend viejo no manda nada → null/0, nunca rompe.
 */
import { describe, expect, it } from "vitest";

import { mapBackendCampaign, mapBackendConversation } from "./api";
import {
  backendAdsCampaignSchema,
  backendAttributedConversationSchema,
} from "./contracts";
import { waCostBreakdown } from "./model";

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

const campaign = {
  id: "CAMP_9",
  name: "Día del Padre",
  source_type: "ad",
  started: 2,
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
  meta_campaign_id: "CAMP_9",
  first_resp: null,
  tendency: null,
  days_run: null,
};

const WA = {
  wa_cost_usd_micros: 22_900,
  wa_cost_by_category: {
    service: { count: 12, usd_micros: 9_600 },
    marketing: { count: 1, usd_micros: 12_500 },
    utility: { count: 1, usd_micros: 800 },
  },
  wa_msgs_pending: 1,
};

describe("contratos — costo de WhatsApp", () => {
  it("la campaña parsea el acumulado por categoría", () => {
    const parsed = backendAdsCampaignSchema.parse({ ...campaign, ...WA });
    expect(parsed.wa_cost_usd_micros).toBe(22_900);
    expect(parsed.wa_cost_by_category?.marketing).toEqual({ count: 1, usd_micros: 12_500 });
    expect(parsed.wa_msgs_pending).toBe(1);
  });

  it("un backend viejo (sin los campos) defaultea a null / 0", () => {
    const camp = backendAdsCampaignSchema.parse(campaign);
    expect(camp.wa_cost_usd_micros).toBeNull();
    expect(camp.wa_cost_by_category).toBeNull();
    expect(camp.wa_msgs_pending).toBe(0);
    const conv = backendAttributedConversationSchema.parse(conversation);
    expect(conv.wa_cost_usd_micros).toBeNull();
  });

  it("acepta una categoría que Meta agregue mañana (record abierto)", () => {
    const parsed = backendAttributedConversationSchema.parse({
      ...conversation,
      wa_cost_usd_micros: 500,
      wa_cost_by_category: { categoria_nueva: { count: 1, usd_micros: 500 } },
    });
    expect(parsed.wa_cost_by_category?.categoria_nueva?.usd_micros).toBe(500);
  });

  it("rechaza un costo string (contract drift)", () => {
    expect(() =>
      backendAttributedConversationSchema.parse({ ...conversation, wa_cost_usd_micros: "0.02" }),
    ).toThrow();
  });
});

describe("mappers — costo de WhatsApp", () => {
  it("campaña: snake → camel", () => {
    const c = mapBackendCampaign(backendAdsCampaignSchema.parse({ ...campaign, ...WA }));
    expect(c.waCostUsdMicros).toBe(22_900);
    expect(c.waCostByCategory?.service).toEqual({ count: 12, usdMicros: 9_600 });
    expect(c.waMsgsPending).toBe(1);
  });

  it("conversación: conserva null cuando el episodio no trae dato (≠ costó 0)", () => {
    const c = mapBackendConversation(backendAttributedConversationSchema.parse(conversation));
    expect(c.waCostUsdMicros).toBeNull();
    expect(c.waCostByCategory).toBeNull();
  });
});

describe("waCostBreakdown — desglose ordenado para pintar", () => {
  it("ordena marketing → utility → service → authentication y rotula en español", () => {
    const rows = waCostBreakdown({
      service: { count: 12, usdMicros: 9_600 },
      utility: { count: 1, usdMicros: 800 },
      marketing: { count: 1, usdMicros: 12_500 },
    });
    expect(rows.map((r) => r.key)).toEqual(["marketing", "utility", "service"]);
    expect(rows.map((r) => r.label)).toEqual(["Marketing", "Utilidad", "Servicio"]);
  });

  it("una categoría desconocida va al final con su nombre crudo", () => {
    const rows = waCostBreakdown({
      categoria_nueva: { count: 1, usdMicros: 500 },
      service: { count: 1, usdMicros: 800 },
    });
    expect(rows.map((r) => r.key)).toEqual(["service", "categoria_nueva"]);
    expect(rows[1].label).toBe("categoria_nueva");
  });

  it("`referral_conversion` (la categoría REAL de Meta para la ventana gratis del anuncio) se rotula", () => {
    // Verificado en el vault de prod 2026-09-18: es la única categoría con
    // precio materializado hasta hoy (pricing type `free_entry_point`).
    const rows = waCostBreakdown({
      referral_conversion: { count: 11, usdMicros: 0 },
      service: { count: 2, usdMicros: 1_600 },
    });
    expect(rows.map((r) => r.key)).toEqual(["service", "referral_conversion"]);
    expect(rows[1].label).toBe("Anuncio (72h gratis)");
  });

  it("null → lista vacía", () => {
    expect(waCostBreakdown(null)).toEqual([]);
  });
});
