// NOTE: key root changed from "agent" → "agents" in HU-20260527. Do not hardcode.
export const agentKeys = {
  all: ["agents"] as const,
  list: () => [...agentKeys.all, "list"] as const,
  detail: (id: string) => [...agentKeys.all, "detail", id] as const,
} as const;
