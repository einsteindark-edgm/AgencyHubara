/**
 * Query keys de `catalog-sync` (TanStack Query). Si dos features queryean la
 * misma entity comparten este factory — no duplicar (regla FSD).
 */

export const catalogSyncKeys = {
  all: ["catalog-sync"] as const,
  /** Historial de syncs (panel izquierdo). */
  history: () => [...catalogSyncKeys.all, "history"] as const,
  /** Estado + step-by-step de UN run, por workflow_id. */
  status: (workflowId: string) =>
    [...catalogSyncKeys.all, "status", workflowId] as const,
  /** Estado de la copia local actual (snapshot). */
  snapshot: () => [...catalogSyncKeys.all, "snapshot"] as const,
} as const;
