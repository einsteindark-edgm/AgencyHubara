import { ApiError } from "@/shared/sdk";

import { engineDecisionsSchema } from "./contracts";
import type { EngineDecision } from "./model";

/**
 * Nombres de los bots de una corrida (plan §3.2), en palabras del operador
 * (revisión 2026-09-29). B0 es el workflow nuevo (V2) con las reglas de hoy:
 * tiene que dar lo mismo que A1 (motor de decisiones §08); B es el workflow
 * nuevo con Jev.
 */
export const ARM_LABELS: Record<string, string> = {
  A0: "Producción",
  A1: "Bot actual simulado",
  B0: "Bot nuevo sin Jev",
  B: "Bot nuevo con Jev",
};

/** Qué es cada bot, en una frase (ayuda del selector). */
export const ARM_HELP: Record<string, string> = {
  A0: "Lo que respondió el bot de verdad.",
  A1: "El mismo código de producción, repetido aquí como control.",
  B0: "El bot nuevo con las reglas de hoy, sin Jev.",
  B: "El bot nuevo, con Jev decidiendo.",
};

/**
 * El nombre del bot en palabras. Un brazo con paquete de decisión
 * (`B@ventas-2`, PAQUETES_DE_DECISION.md F6) es ese bot con otra inteligencia.
 */
export function armLabel(arm: string): string {
  const at = arm.indexOf("@");
  if (at > 0) {
    const bot = arm.slice(0, at);
    return `${ARM_LABELS[bot] ?? bot} · paquete ${arm.slice(at + 1)}`;
  }
  return ARM_LABELS[arm] ?? arm;
}

/**
 * La ayuda del selector para un bot. Un brazo con paquete (`B@ventas-2`) es el
 * mismo bot leyendo otras reglas: sin esto la ayuda quedaba en blanco.
 */
export function armHelp(arm: string): string {
  const at = arm.indexOf("@");
  if (at > 0) {
    const base = ARM_HELP[arm.slice(0, at)];
    const bundle = `Usa las reglas del paquete ${arm.slice(at + 1)} en lugar de las de la tienda.`;
    return base ? `${base} ${bundle}` : bundle;
  }
  return ARM_HELP[arm] ?? "";
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

// ── Decisiones del motor en un turno (bot nuevo) ─────────────────────────────

/** Las decisiones del motor de la traza de un turno; las que no tienen forma se descartan. */
export function engineDecisionsOf(trace: Record<string, unknown>): EngineDecision[] {
  return engineDecisionsSchema.parse(trace.decisions);
}

// El lenguaje de las decisiones y los checks por su nombre viven en
// `@/shared/lib` (Calidad LLM de Agents muestra lo mismo, 2026-10-02).
export {
  capabilityLabel,
  checksHeadline,
  checkView,
  customerLabel,
  decidedByLabel,
  decisionStageLabel,
  formatDecisionValue,
  formatUsd,
  jevFailed,
  LEVEL_HELP,
  levelLabel,
  turnVerdict,
  worstVerdict,
  type CheckView,
} from "@/shared/lib";
