export const audienceKeys = {
  all: ["marketing-audience"] as const,
  // `updatedAtMs` en la key: al commitear segmentos (PUT) cambia el
  // updated_at_ms de la campaña → key nueva → refetch de la audiencia sin
  // acoplar la mutación de campaign a esta entity.
  forCampaign: (campaignId: string, updatedAtMs: number) =>
    [...audienceKeys.all, "campaign", campaignId, updatedAtMs] as const,
  conversation: (sessionId: string) =>
    [...audienceKeys.all, "conversation", sessionId] as const,
} as const;
