/**
 * Query key factory del dominio ad-analysis-run. Las features y el page del
 * plugin queryean por estas keys — nunca array literals sueltos (las
 * invalidaciones del stream targetean la factory).
 */
export const adAnalysisRunKeys = {
  all: ["ad-analysis-run"] as const,
  agents: () => [...adAnalysisRunKeys.all, "agents"] as const,
  detail: (runId: string) =>
    [...adAnalysisRunKeys.all, "detail", runId] as const,
} as const;
