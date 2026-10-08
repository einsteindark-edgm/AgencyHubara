/** TanStack Query key factory para las evaluaciones por episodio. */
export const episodeEvalKeys = {
  all: ["episode-evals"] as const,
  list: (days: number, suite: string, bot: string | null = null) =>
    [...episodeEvalKeys.all, "list", days, suite, bot ?? "todos"] as const,
  transcript: (sessionId: string, episodeId: string) =>
    [...episodeEvalKeys.all, "transcript", sessionId, episodeId] as const,
} as const;
