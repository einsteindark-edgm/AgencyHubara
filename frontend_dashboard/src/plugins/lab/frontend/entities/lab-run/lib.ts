import { ApiError } from "@/shared/sdk";

import { engineDecisionsSchema } from "./contracts";
import type { EngineDecision, EpisodeVerdict } from "./model";

/**
 * Nombres de los bots de una corrida (plan §3.2, diseño §09). B0 es el
 * workflow nuevo (V2) con las reglas de hoy: tiene que dar lo mismo que A1
 * (motor de decisiones §08); B es el workflow nuevo con Jev.
 */
export const ARM_LABELS: Record<string, string> = {
  A0: "Producción",
  A1: "Actual simulado",
  B0: "Nuevo sin Jev",
  B: "Nuevo + Jev",
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

/** Dólares con coma decimal; `digits` sube la precisión (costo por turno: 4). */
export function formatUsd(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `US$${value.toLocaleString("es-CO", { maximumFractionDigits: digits })}`;
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

/** Mismos nombres que el panel «Motor de decisiones» de Agents. */
const CAPABILITY_LABELS: Record<string, string> = {
  compra: "Compra",
  retoma: "Retoma",
  baja: "Baja",
  acuse: "Acuse tras la despedida",
  cupon: "Cupón",
  fuera_de_catalogo: "Fuera de catálogo",
  cantidad: "Cantidad",
  categoria: "Categoría",
  familia_de_color: "Familia de color",
  item_del_pedido: "Ítem del pedido",
  zona_de_envio: "Zona de envío",
  datos: "Datos de envío",
  producto_nombrado: "Producto nombrado",
  persona: "Persona",
  enumeracion: "Enumeración",
  monto: "Monto",
  selector: "Selector",
  contactar: "Contactar",
  cierre: "Cierre por abandono",
  afirmacion: "Afirmación",
  preambulo: "Preámbulo del modelo",
  destinatario: "Destinatario",
  rescate: "Rescate",
  portavelas: "Portavelas",
  saludo: "Saludo",
};

export function capabilityLabel(id: string): string {
  return CAPABILITY_LABELS[id] ?? id;
}

/** Quién decidió: Jev, la regla, o la regla porque Jev falló o dudó (y por qué). */
export function decidedByLabel(d: Pick<EngineDecision, "by" | "provider" | "reason">): string {
  if (d.by === "jev") return "Jev";
  if (d.by === "piso") return "Piso de la regla";
  if (d.by === "respaldo") {
    if (d.reason === "duda") return "Regla (Jev dudó)";
    if (d.reason === "no_question") return "Regla (nada que preguntar)";
    return `Regla (Jev falló: ${d.reason || "error"})`;
  }
  return d.provider === "sombra" ? "Regla (Jev en sombra)" : "Regla";
}

/** Jev falló (error, timeout, sin llave, otro modelo): la regla decidió por él. */
export function jevFailed(d: Pick<EngineDecision, "by" | "reason">): boolean {
  return d.by === "respaldo" && d.reason !== "duda" && d.reason !== "no_question";
}

export function decisionStageLabel(d: Pick<EngineDecision, "stage" | "message">): string {
  if (d.stage === "ingest") return d.message !== undefined ? `Lectura del mensaje ${d.message}` : "Lectura de la ráfaga";
  if (d.stage === "complemento") return "Complemento";
  return "Turno";
}

export function formatDecisionValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "sí" : "no";
  if (Array.isArray(value)) return value.length ? value.map((v) => formatDecisionValue(v)).join(", ") : "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
