/** TanStack Query key factory de Calidad LLM sobre producción. */
export const productionQualityKeys = {
  all: ["production-quality"] as const,
  conversations: (days: number, bot: string | null) => [...productionQualityKeys.all, "conversations", days, bot ?? "todos"] as const,
  thread: (sid: string) => [...productionQualityKeys.all, "thread", sid] as const,
  trace: (sid: string, turnKey: string) => [...productionQualityKeys.all, "trace", sid, turnKey] as const,
  evaluations: (sid: string) => [...productionQualityKeys.all, "evaluations", sid] as const,
  jev: (days: number, bot: string) => [...productionQualityKeys.all, "jev", days, bot] as const,
} as const;
