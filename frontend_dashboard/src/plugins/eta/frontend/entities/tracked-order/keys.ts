export const trackedOrderKeys = {
  all: ["tracked-order"] as const,
  list: () => [...trackedOrderKeys.all, "list"] as const,
} as const;
