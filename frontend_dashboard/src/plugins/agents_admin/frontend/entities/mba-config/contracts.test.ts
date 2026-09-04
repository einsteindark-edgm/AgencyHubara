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
  });

  it("rejects a payload without settings (the boundary is strict)", () => {
    const broken: Record<string, unknown> = { ...MBA_CONFIG_FIXTURE };
    delete broken.settings;
    expect(() => mbaConfigSchema.parse(broken)).toThrow();
  });
});
