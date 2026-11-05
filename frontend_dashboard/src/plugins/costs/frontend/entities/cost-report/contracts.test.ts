import { describe, expect, it } from "vitest";

import { backendCatalogSchema, backendProviderReportSchema } from "./contracts";
import {
  CATALOG_FIXTURE,
  DEEPSEEK_REPORT_FIXTURE,
  NOT_CONNECTED_FIXTURE,
} from "./fixtures";

describe("cost-report contracts", () => {
  it("parses the catalog the backend sends", () => {
    const parsed = backendCatalogSchema.parse(CATALOG_FIXTURE);
    expect(parsed.providers.map((p) => p.id)).toEqual(["deepseek", "openrouter", "whatsapp"]);
    expect(parsed.max_days).toBe(92);
  });

  it("parses a provider report with lines in several currencies", () => {
    const parsed = backendProviderReportSchema.parse(DEEPSEEK_REPORT_FIXTURE);
    expect(parsed.lines).toHaveLength(3);
    expect(parsed.lines[1].currency).toBe("CNY");
  });

  it("keeps a missing total as null, never as zero", () => {
    const parsed = backendProviderReportSchema.parse(NOT_CONNECTED_FIXTURE);
    expect(parsed.production_usd_micros).toBeNull();
  });

  it("tolerates an unknown status or source from a newer backend (L-10)", () => {
    const parsed = backendProviderReportSchema.parse({
      ...DEEPSEEK_REPORT_FIXTURE,
      status: "throttled",
      source: "estimated",
      extra_field: 1,
    });
    expect(parsed.status).toBe("error");
    expect(parsed.source).toBeNull();
  });
});
