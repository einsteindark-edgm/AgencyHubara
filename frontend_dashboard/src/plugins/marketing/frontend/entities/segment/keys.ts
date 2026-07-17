export const segmentKeys = {
  all: ["marketing-segment"] as const,
  info: () => [...segmentKeys.all, "info"] as const,
} as const;
