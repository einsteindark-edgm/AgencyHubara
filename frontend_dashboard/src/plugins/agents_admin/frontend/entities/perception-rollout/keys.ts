/** TanStack Query key factory del encendido del bot nuevo. */
export const rolloutKeys = {
  all: ["perception-rollout"] as const,
  current: () => [...rolloutKeys.all, "current"] as const,
} as const;
