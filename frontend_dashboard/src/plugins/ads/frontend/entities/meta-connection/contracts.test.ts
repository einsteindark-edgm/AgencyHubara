import { describe, expect, it } from "vitest";

import {
  backendMetaInsightsSchema,
  backendMetaStatusSchema,
} from "./contracts";

describe("meta-connection contracts", () => {
  it("parsea el status desconectado", () => {
    const p = backendMetaStatusSchema.parse({ connected: false });
    expect(p.connected).toBe(false);
  });

  it("parsea el status conectado con la cuenta + expired/can_manage", () => {
    const p = backendMetaStatusSchema.parse({
      connected: true,
      account_id: "act_1010393601284112",
      account_name: "Hubara",
      scopes: ["ads_read", "ads_management"],
      expires_at: 1782842400,
      expired: false,
      can_manage: true,
    });
    expect(p).toMatchObject({
      connected: true,
      account_name: "Hubara",
      expired: false,
      can_manage: true,
    });
  });

  it("parsea insights con campañas (números reales)", () => {
    const p = backendMetaInsightsSchema.parse({
      connected: true,
      account_id: "act_1010393601284112",
      account_name: "Hubara",
      since: "2026-06-01",
      until: "2026-06-30",
      campaigns: [
        {
          campaign_id: "c1",
          name: "Duo zodiacal",
          status: "ACTIVE",
          objective: "OUTCOME_SALES",
          spend: 896823,
          impressions: 45000,
          reach: 38000,
          clicks: 571,
          messaging_conversations_started: 205,
        },
      ],
    });
    expect(p.campaigns[0].clicks).toBe(571);
  });

  it("parsea insights desconectado", () => {
    const p = backendMetaInsightsSchema.parse({ connected: false, campaigns: [] });
    expect(p.campaigns).toEqual([]);
  });
});
