export const agentKeys = {
  all: ["agent"] as const,
  list: () => [...agentKeys.all, "list"] as const,
  detail: (id: string) => [...agentKeys.all, "detail", id] as const,
  personalities: () => [...agentKeys.all, "personalities"] as const,
} as const;
