import type { MetaInsightsParams } from "./model";

export const metaConnectionKeys = {
  all: ["meta-connection"] as const,
  status: () => [...metaConnectionKeys.all, "status"] as const,
  insights: (p: MetaInsightsParams) =>
    [...metaConnectionKeys.all, "insights", p.days ?? null, p.since ?? null, p.until ?? null] as const,
  analysisInput: () => [...metaConnectionKeys.all, "analysis-input"] as const,
} as const;
