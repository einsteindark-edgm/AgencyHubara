export const mbaAgentKeys = {
  all: ["mba-agent"] as const,
  list: () => [...mbaAgentKeys.all, "list"] as const,
} as const;
