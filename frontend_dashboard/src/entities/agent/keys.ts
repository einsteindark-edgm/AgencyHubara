export const agentKeys = {
  all: ["agent"] as const,
  list: () => [...agentKeys.all, "list"] as const,
} as const;
