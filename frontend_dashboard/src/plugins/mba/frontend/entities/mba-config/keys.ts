export const mbaConfigKeys = {
  all: ["mba-config"] as const,
  detail: (agentId: string) => [...mbaConfigKeys.all, agentId] as const,
} as const;
