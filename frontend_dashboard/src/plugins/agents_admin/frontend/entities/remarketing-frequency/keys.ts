/** TanStack Query key factory de la frecuencia del remarketing. */
export const frequencyKeys = {
  all: ["remarketing-frequency"] as const,
  current: () => [...frequencyKeys.all, "current"] as const,
} as const;
