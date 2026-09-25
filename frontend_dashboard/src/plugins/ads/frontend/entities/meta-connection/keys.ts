import type { MetaAnalysisInputParams, MetaInsightsParams } from "./model";

export const metaConnectionKeys = {
  all: ["meta-connection"] as const,
  status: () => [...metaConnectionKeys.all, "status"] as const,
  insights: (p: MetaInsightsParams) =>
    [...metaConnectionKeys.all, "insights", p.days ?? null, p.since ?? null, p.until ?? null] as const,
  analysisInput: (p: MetaAnalysisInputParams) =>
    [
      ...metaConnectionKeys.all,
      "analysis-input",
      p.campaignId ?? null,
      p.days === undefined ? "default" : p.days,
      p.from ?? null,
      p.to ?? null,
    ] as const,
} as const;
