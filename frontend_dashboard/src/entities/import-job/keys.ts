export const importJobKeys = {
  all: ["import-job"] as const,
  list: () => [...importJobKeys.all, "list"] as const,
} as const;
