/** TanStack Query key factory para las evaluaciones por episodio. */
export const episodeEvalKeys = {
  all: ["episode-evals"] as const,
  list: (days: number, suite: string) =>
    [...episodeEvalKeys.all, "list", days, suite] as const,
  transcript: (sessionId: string, episodeId: string) =>
    [...episodeEvalKeys.all, "transcript", sessionId, episodeId] as const,
} as const;
