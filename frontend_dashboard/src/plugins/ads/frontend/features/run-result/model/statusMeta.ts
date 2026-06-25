/**
 * Metadata de presentación por status del run (label en español + token de
 * color del @theme). Pura y testeable — la UI solo la consume. Vive en la
 * feature (no en la entity) porque es presentación, no dominio: otra feature
 * que necesitara estos labels los pediría a la entity, pero hoy solo los usa
 * `run-result`.
 */

import type { RunStatus } from "@plugins/ads/frontend/entities/ad-analysis-run";

export interface StatusMeta {
  label: string;
  /** Token CSS del @theme (var(--color-*)). */
  color: string;
}

const STATUS_META: Record<RunStatus, StatusMeta> = {
  pending: { label: "Pendiente", color: "var(--color-fg-muted)" },
  running: { label: "Ejecutando", color: "var(--color-info)" },
  awaiting_approval: { label: "Esperando aprobación", color: "var(--color-warn)" },
  completed: { label: "Completado", color: "var(--color-ok)" },
  failed: { label: "Falló", color: "var(--color-danger)" },
};

export function statusMeta(status: RunStatus): StatusMeta {
  return STATUS_META[status];
}
