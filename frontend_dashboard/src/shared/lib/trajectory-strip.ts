import type { QualityCheckVerdict, QualityLevel, QualityStatus } from "./quality-view";

/**
 * Modelo de vista de la tira de trayectoria (`@/shared/ui` → `TrajectoryStrip`):
 * columnas por turno con carriles de chips, bandas de etapa y checks anclados.
 * Genérico: el dueño del dominio (la entity `scorecard` de cada plugin) arma
 * este modelo desde su trayectoria y resuelve rótulos y colores de etapa.
 */

export type StripLaneId =
  | "cliente"
  | "bot"
  | "tools"
  | "componentes"
  | "estado"
  | "guardas"
  | "checks";

export type StripEventLaneId = Exclude<StripLaneId, "checks">;

export const STRIP_LANES: ReadonlyArray<{ id: StripLaneId; label: string }> = [
  { id: "cliente", label: "cliente" },
  { id: "bot", label: "bot" },
  { id: "tools", label: "tools" },
  { id: "componentes", label: "componentes" },
  { id: "estado", label: "estado" },
  { id: "guardas", label: "guardas" },
  { id: "checks", label: "checks" },
];

export type StripChipKind =
  | "customer"
  | "system"
  | "handoff"
  | "signal"
  | "sent"
  | "suppressed"
  | "discarded"
  | "tool_ok"
  | "tool_rejected"
  | "tool_unknown"
  | "intent"
  | "tag"
  | "route"
  | "confirmed"
  | "guard";

export interface StripChip {
  kind: StripChipKind;
  /** Texto corto visible en el chip. */
  text: string;
  /** Texto completo para el tooltip. */
  detail: string;
  /** Estado señalado por un check fallado en el mismo turno. */
  alert: boolean;
}

export interface StripCheck {
  checkId: string;
  name: string;
  verdict: QualityCheckVerdict;
  level: QualityLevel;
  status: QualityStatus;
  /** false = el check no trae turno y se ancla al último. */
  anchored: boolean;
}

export interface StripColumn {
  index: number;
  turn: number;
  atMs: number | null;
  trigger: string;
  /** Rótulo del disparador tras `turno N` (null = turno normal del cliente, sin nota). */
  triggerNote: string | null;
  /** Turno sin texto del bot esperado (p. ej. silencio del cliente): el carril bot dice "sin texto". */
  botSilent: boolean;
  stage: string | null;
  /** Hueco (ms) desde el turno anterior cuando supera el umbral del dominio. */
  gapBeforeMs: number | null;
  lanes: Record<StripEventLaneId, StripChip[]>;
  checks: StripCheck[];
  isFirstFailure: boolean;
  isFirstCritical: boolean;
}

export interface StageBand {
  stage: string | null;
  label: string;
  /** Token de color de la etapa (`var(--color-*)`). */
  color: string;
  from: number;
  to: number;
}

export interface StripPoint {
  turn: number;
  checkId: string;
}

export interface StripModel {
  columns: StripColumn[];
  bands: StageBand[];
  firstFailure: StripPoint | null;
  firstCritical: StripPoint | null;
}
