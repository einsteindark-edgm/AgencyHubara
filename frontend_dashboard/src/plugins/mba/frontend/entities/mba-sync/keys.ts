export const mbaSyncKeys = {
  all: ["mba-sync"] as const,
  state: (agentId: string) => [...mbaSyncKeys.all, "state", agentId] as const,
  plan: (agentId: string) => [...mbaSyncKeys.all, "plan", agentId] as const,
} as const;
