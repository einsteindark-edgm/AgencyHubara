/** TanStack Query key factory del scorecard por etapa. */
export const scorecardKeys = {
  all: ["scorecards"] as const,
  registry: () => [...scorecardKeys.all, "registry"] as const,
  lists: () => [...scorecardKeys.all, "list"] as const,
  list: (days: number) => [...scorecardKeys.lists(), days] as const,
  details: () => [...scorecardKeys.all, "detail"] as const,
  detail: (sessionId: string, episodeId: string) =>
    [...scorecardKeys.details(), sessionId, episodeId] as const,
} as const;
