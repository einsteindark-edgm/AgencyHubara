import { describe, it, expect } from "vitest";
import { mbaConfigSchema } from "./contracts";
import { MBA_CONFIG_FIXTURE } from "./fixture";

describe("mbaConfigSchema", () => {
  it("parses the real shape of GET /api/agents/{id}/mba-config", () => {
    const parsed = mbaConfigSchema.parse(MBA_CONFIG_FIXTURE);
    expect(parsed.agent_id).toBe("sales");
    expect(parsed.skills[0].title).toBe("persona-y-tono");
    expect(parsed.skills[2].over_limit).toBe(true);
    expect(parsed.settings.never_say_phrases[0]).toEqual({ phrase: "vos", source: "IDENTITY.md" });
    expect(parsed.business_info.contact_info.email).toBeNull();
    // las llamadas exactas a Meta, numeradas, con el body como objeto JSON literal
    expect(parsed.workspace).toBe("hubara_agency/src/plugins/chats/agent/sales/workspace");
    expect(parsed.requests.map((r) => r.step)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
    expect(parsed.requests[2].body).toEqual({
      title: "persona-y-tono",
      description: "Aplicar en toda conversación: quién es el asesor.",
      skill: "# Eres el Asesor\n\nEres un experto de ventas.",
    });
    expect(parsed.requests[0].headers["X-API-Version"]).toBe("2.0.0");
  });

  it("rejects a payload without requests (the exact calls are the contract)", () => {
    const broken: Record<string, unknown> = { ...MBA_CONFIG_FIXTURE };
    delete broken.requests;
    expect(() => mbaConfigSchema.parse(broken)).toThrow();
  });

  it("rejects a payload without settings (the boundary is strict)", () => {
    const broken: Record<string, unknown> = { ...MBA_CONFIG_FIXTURE };
    delete broken.settings;
    expect(() => mbaConfigSchema.parse(broken)).toThrow();
  });
});
