/** TanStack Query key factory para candidatos a golden. */
export const evalCandidateKeys = {
  all: ["eval-candidates"] as const,
  list: () => [...evalCandidateKeys.all, "list"] as const,
  detail: (id: string) => [...evalCandidateKeys.all, "detail", id] as const,
} as const;
