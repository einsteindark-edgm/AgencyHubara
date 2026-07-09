/**
 * Query key factory del dominio ad-analysis-run. Las features y el page del
 * plugin queryean por estas keys — nunca array literals sueltos (las
 * invalidaciones del stream targetean la factory).
 */
export const adAnalysisRunKeys = {
  all: ["ad-analysis-run"] as const,
  agents: () => [...adAnalysisRunKeys.all, "agents"] as const,
  lists: () => [...adAnalysisRunKeys.all, "list"] as const,
  // El historial es POR CAMPAÑA — sin id, el historial completo.
  list: (campaignId?: string) =>
    [...adAnalysisRunKeys.lists(), campaignId ?? "all"] as const,
  detail: (runId: string) =>
    [...adAnalysisRunKeys.all, "detail", runId] as const,
} as const;
