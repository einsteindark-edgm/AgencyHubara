/** TanStack Query key factory para etiquetas humanas + calibración del juez. */
export const evalLabelKeys = {
  all: ["eval-labels"] as const,
  labels: (sessionId: string, episodeId: string) =>
    [...evalLabelKeys.all, "labels", sessionId, episodeId] as const,
  queue: (days: number, limit: number) =>
    [...evalLabelKeys.all, "queue", days, limit] as const,
  calibration: () => [...evalLabelKeys.all, "calibration"] as const,
} as const;
