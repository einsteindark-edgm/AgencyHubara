import type { CalibrationStatus } from "./model";

export function formatRate(rate: number | null | undefined): string {
  return rate === null || rate === undefined ? "—" : `${Math.round(rate * 100)} %`;
}

export function formatKappa(kappa: number | null | undefined): string {
  return kappa === null || kappa === undefined ? "—" : kappa.toFixed(2);
}

const STATUS_LABELS: Record<CalibrationStatus, string> = {
  confiable: "confiable",
  revisar: "revisar",
  sin_datos: "sin datos",
};

export function calibrationStatusLabel(status: CalibrationStatus): string {
  return STATUS_LABELS[status];
}

const REASON_LABELS: Record<string, string> = {
  desconocido: "el juez no decidió",
  falla: "el juez dijo falla",
  muestra: "muestra aleatoria",
};

/** Por qué un item está en la cola de etiquetado. */
export function queueReasonLabel(reason: string): string {
  return REASON_LABELS[reason] ?? reason;
}
