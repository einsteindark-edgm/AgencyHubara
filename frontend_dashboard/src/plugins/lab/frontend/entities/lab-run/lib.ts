import { ApiError } from "@/shared/sdk";

import { engineDecisionsSchema } from "./contracts";
import type { CheckCatalog, CheckLevel, EngineDecision, EpisodeVerdict, EvalResult } from "./model";

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

/** Por qué decidió la regla aunque Jev estaba encendido (`reason` del respaldo). */
const FALLBACK_REASONS: Record<string, string> = {
  duda: "Jev dudó",
  no_question: "nada que preguntarle a Jev",
  timeout: "Jev no respondió a tiempo",
  no_api_key: "falta la llave de Jev",
  http_402: "Jev sin saldo",
  http_429: "Jev saturado",
  disabled: "Jev apagado",
  model_changed: "Jev cambió de modelo",
  unknown_profile: "perfil de Jev desconocido",
  sin_contexto: "no hubo promoción reciente",
};

/** Quién decidió: Jev, la regla, o la regla porque Jev falló o dudó (y por qué). */
export function decidedByLabel(d: Pick<EngineDecision, "by" | "provider" | "reason">): string {
  if (d.by === "jev") return "Jev";
  if (d.by === "piso") return "La regla corrigió a Jev";
  if (d.by === "respaldo") {
    const why = d.reason ? FALLBACK_REASONS[d.reason] : undefined;
    return why ? `La regla (${why})` : `La regla (Jev falló: ${d.reason || "error"})`;
  }
  return d.provider === "sombra" ? "La regla (Jev en sombra)" : "La regla";
}

/** Jev falló (error, timeout, sin llave, otro modelo): la regla decidió por él. */
export function jevFailed(d: Pick<EngineDecision, "by" | "reason">): boolean {
  return d.by === "respaldo" && d.reason !== "duda" && d.reason !== "no_question";
}

export function decisionStageLabel(d: Pick<EngineDecision, "stage" | "message">): string {
  if (d.stage === "ingest") return d.message !== undefined ? `Al leer el mensaje ${d.message}` : "Al leer los mensajes";
  if (d.stage === "complemento") return "En el mensaje de complemento";
  return "Durante el turno";
}

export function formatDecisionValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "sí" : "no";
  if (Array.isArray(value)) return value.length ? value.map((v) => formatDecisionValue(v)).join(", ") : "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

// ── Checks por su nombre ─────────────────────────────────────────────────────

const LEVEL_NAME: Record<CheckLevel, string> = { critico: "crítico", mayor: "mayor", menor: "menor" };

/** Qué hace cada nivel con el veredicto (misma regla que `scorecard/verdict.py`). */
export const LEVEL_HELP: Record<CheckLevel, string> = {
  critico: "Si falla, la conversación reprueba.",
  mayor: "Si falla, la conversación queda en alerta.",
  menor: "No cambia el resultado; sirve para priorizar.",
};

export function levelLabel(level: CheckLevel): string {
  return LEVEL_NAME[level];
}

/** Un check listo para mostrar: su nombre, lo que se esperaba y por qué falló. */
export interface CheckView {
  id: string;
  name: string;
  /** El nivel que contó para el veredicto. */
  level: CheckLevel;
  /** Por qué el nivel no es el del registro (un crítico de juez sin calibrar cuenta como mayor). */
  levelNote: string | null;
  /** Lo que se esperaba (la regla del registro). */
  rule: string;
  /** Por qué falló (o pasó): la crítica del juez o la evidencia del check de código. */
  reason: string | null;
  /** Lo que dijo el bot, citado por el juez. */
  quote: string | null;
  byJudge: boolean;
}

function cleanText(value: string | null | undefined): string | null {
  const text = (value ?? "").trim();
  return text === "" ? null : text;
}

/** La evidencia de un check de código empieza con «turno N: »; en la vista del turno sobra. */
function withoutTurn(text: string): string {
  const rest = text.replace(/^turno \d+:\s*/i, "");
  return rest.charAt(0).toUpperCase() + rest.slice(1);
}

function specOf(catalog: CheckCatalog | undefined, id: string) {
  return catalog?.checks.find((c) => c.id === id);
}

export function checkView(catalog: CheckCatalog | undefined, result: EvalResult): CheckView {
  const spec = specOf(catalog, result.check_id);
  const level = result.level ?? spec?.level ?? "menor";
  let levelNote: string | null = null;
  if (spec && spec.level !== level) {
    levelNote =
      spec.kind === "judge" && spec.level === "critico"
        ? `Es ${LEVEL_NAME.critico}, pero cuenta como ${LEVEL_NAME[level]} mientras el juez no esté calibrado.`
        : `Es ${LEVEL_NAME[spec.level]}, pero en esta corrida contó como ${LEVEL_NAME[level]}.`;
  }
  const critique = cleanText(result.critique);
  const evidence = cleanText(result.evidence);
  return {
    id: result.check_id,
    name: spec?.name || result.check_id,
    level,
    levelNote,
    rule: spec?.rule ?? "",
    reason: critique ?? (evidence ? withoutTurn(evidence) : null),
    quote: critique && evidence ? evidence : null,
    byJudge: result.source === "judge" || (!result.source && spec?.kind === "judge"),
  };
}

/** «Fallan 2 de 3 checks», «Falla 1 de 3 checks» o «Cumple los 3 checks». */
export function checksHeadline(failing: number, decided: number): string {
  if (decided === 0) return "Ningún check se pudo decidir";
  if (failing === 0) return decided === 1 ? "Cumple el único check" : `Cumple los ${decided} checks`;
  return `${failing === 1 ? "Falla" : "Fallan"} ${failing} de ${decided} checks`;
}

/**
 * El resultado de un turno con la regla del scorecard: un crítico que falla
 * reprueba (FALLA), un mayor deja en alerta (ALERTA), los menores no cambian
 * nada (PASA). Sin checks decididos (todos sin señal o sin aplicar): SIN_DATOS.
 */
export function turnVerdict(catalog: CheckCatalog | undefined, results: EvalResult[]): EpisodeVerdict {
  const decided = results.filter((r) => r.verdict === "pasa" || r.verdict === "falla");
  if (decided.length === 0) return "SIN_DATOS";
  const levels = decided.filter((r) => r.verdict === "falla").map((r) => r.level ?? specOf(catalog, r.check_id)?.level ?? "menor");
  if (levels.includes("critico")) return "FALLA";
  if (levels.includes("mayor")) return "ALERTA";
  return "PASA";
}
