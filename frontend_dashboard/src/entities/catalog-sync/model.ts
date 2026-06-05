/**
 * Helpers de presentación de `catalog-sync`, compartidos por las 3 features
 * del plugin (sync-history, sync-runner, sync-inspector). Viven en la entity
 * para no duplicarlos ni hacer cross-feature imports (anti-pattern FSD §2).
 * Son funciones puras — sin estado, sin fetch.
 */

import type { SyncStatus } from "./contracts";

/** Orden canónico de los 3 pasos reales del CatalogSyncWorkflow. */
export const SYNC_STEP_KEYS = ["pull", "write", "push"] as const;

/** ¿El sync sigue corriendo? Decide si vale la pena seguir polleando. */
export function isSyncActive(status: SyncStatus): boolean {
  return status === "running";
}

/** Etiqueta humana del estado de un run. */
export function syncStatusLabel(status: SyncStatus): string {
  switch (status) {
    case "running":
      return "En curso";
    case "completed":
      return "Completado";
    case "failed":
      return "Falló";
    case "cancelled":
      return "Cancelado";
    default:
      return "Desconocido";
  }
}

/** Tono visual por estado (reusa la paleta `ok/err/live/mute` del kit). */
export type SyncTone = "ok" | "err" | "live" | "mute";

export function syncStatusTone(status: SyncStatus): SyncTone {
  switch (status) {
    case "running":
      return "live";
    case "completed":
      return "ok";
    case "failed":
    case "cancelled":
      return "err";
    default:
      return "mute";
  }
}

/** "hace 2 min" / "hace 5 h" / "hace 3 días" a partir de un epoch ms. */
export function formatRelativeTime(ms: number | null): string {
  if (ms === null) return "—";
  const diffSec = Math.max(0, Math.floor((Date.now() - ms) / 1000));
  if (diffSec < 60) return "hace un momento";
  const min = Math.floor(diffSec / 60);
  if (min < 60) return `hace ${min} min`;
  const hours = Math.floor(min / 60);
  if (hours < 24) return `hace ${hours} h`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `hace ${days} ${days === 1 ? "día" : "días"}`;
  const months = Math.floor(days / 30);
  return `hace ${months} ${months === 1 ? "mes" : "meses"}`;
}
