export const mbaRolloutKeys = {
  all: ["mba-rollout"] as const,
  status: (agentId: string) => [...mbaRolloutKeys.all, agentId] as const,
} as const;
