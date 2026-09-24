import { ApiError } from "@/shared/api";

import type { EpisodeVerdict } from "./model";

/** Nombres de los bots de una corrida (plan §3.2, diseño §09). */
export const ARM_LABELS: Record<string, string> = {
  A0: "Producción",
  A1: "Actual simulado",
  B: "Nuevo + Jev",
  C: "Nuevo + OpenAI",
};

export function armLabel(arm: string): string {
  return ARM_LABELS[arm] ?? arm;
}

/** El cliente sin su número completo: solo los 4 últimos caracteres. */
export function customerLabel(sessionId: string): string {
  return `Cliente ···${sessionId.slice(-4)}`;
}

const RANK: Record<EpisodeVerdict, number> = { FALLA: 0, ALERTA: 1, PASA: 2, SIN_DATOS: 3 };

/** El peor veredicto de varios episodios (FALLA > ALERTA > PASA > SIN_DATOS). */
export function worstVerdict(verdicts: EpisodeVerdict[]): EpisodeVerdict {
  return verdicts.reduce<EpisodeVerdict>((worst, v) => (RANK[v] < RANK[worst] ? v : worst), "SIN_DATOS");
}

export function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `US$${value.toLocaleString("es-CO", { maximumFractionDigits: 2 })}`;
}

/**
 * Status y mensaje de un error del API. FastAPI manda `{detail: "…"}` o, en
 * los candados del lanzador, `{detail: {message|reason, …}}`.
 */
export function apiErrorDetail(error: unknown): { status: number | null; message: string | null } {
  if (!(error instanceof ApiError)) return { status: null, message: null };
  const body = error.body as { detail?: unknown } | null;
  const detail = body && typeof body === "object" ? body.detail : undefined;
  if (typeof detail === "string") return { status: error.status, message: detail };
  if (detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string") {
    return { status: error.status, message: (detail as { message: string }).message };
  }
  return { status: error.status, message: null };
}
