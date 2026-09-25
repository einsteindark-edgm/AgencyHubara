/**
 * Envío de una campaña de WhatsApp en Ads (2026-09-25): contrato + mapper.
 *
 * El fixture es el shape REAL que emite el backend
 * (`hubara_agency/tests/plugins/ads/test_campaign_send_stats.py` lo verifica
 * por HTTP): `whatsapp_send` solo en filas `hubara_campaign`. Un backend viejo
 * no lo manda → null, nunca rompe el parseo de la campaña.
 */
import { describe, expect, it } from "vitest";

import { mapBackendCampaign } from "./api";
import { backendAdsCampaignSchema } from "./contracts";

const campaign = {
  id: "mkt-amor",
  name: "Amor y amistad",
  source_type: "hubara_campaign",
  started: 12,
  first_seen_ms: 1790000000000,
  last_seen_ms: 1790003600000,
  conversations: null,
  revenue: 94000,
  avg_ticket: 47000,
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

const SEND = {
  sent: 100,
  delivered: 90,
  read: 60,
  failed: 3,
  replied: 12,
  opted_out: 2,
  cost_usd_micros: 1_125_000,
  cost_pending: 7,
  untracked: 0,
};

describe("contratos — envío de una campaña de WhatsApp", () => {
  it("la fila de la campaña trae su envío y el mapper lo pasa al modelo", () => {
    const parsed = backendAdsCampaignSchema.parse({ ...campaign, whatsapp_send: SEND });

    expect(mapBackendCampaign(parsed).whatsappSend).toEqual({
      sent: 100,
      delivered: 90,
      read: 60,
      failed: 3,
      replied: 12,
      optedOut: 2,
      costUsdMicros: 1_125_000,
      costPending: 7,
      untracked: 0,
    });
  });

  it("un backend viejo (sin el campo) no rompe: queda null", () => {
    const parsed = backendAdsCampaignSchema.parse(campaign);

    expect(mapBackendCampaign(parsed).whatsappSend).toBeNull();
  });
});
