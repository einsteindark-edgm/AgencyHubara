/**
 * Los checks del scorecard como los entiende el operador (revisión del
 * laboratorio 2026-09-29: «EST-06 · falla» no le decía nada), compartidos por
 * el laboratorio y Calidad LLM de Agents (2026-10-02: Calidad LLM muestra
 * producción con la vista del laboratorio). Formas de vista genéricas: cada
 * dueño parsea su contrato y pasa estas.
 */

import type { QualityCheckVerdict, QualityLevel, QualityVerdict } from "./quality-view";

/** Un asunto del cliente y si recibió respuesta (EST-08 v2). */
export interface TopicCoverageView {
  topic: string;
  turn: number | null;
  msg: number | null;
  covered: boolean;
  evidence: string;
}

/** El resultado de un check en un turno (o en el episodio). */
export interface EvalResultView {
  check_id: string;
  verdict: QualityCheckVerdict;
  /** El nivel que CONTÓ para el veredicto (un crítico de juez sin calibrar cuenta como mayor). */
  level?: QualityLevel;
  turn: number | null;
  evidence: string | null;
  critique: string | null;
  source: string | null;
  topics: TopicCoverageView[];
}

/** Un check del registro (`sales_eval/scorecard/registry.py`). */
export interface CheckSpecView {
  id: string;
  name: string;
  level: QualityLevel;
  kind: string;
  rule: string;
  stage?: string;
}

/** Lo que el operador necesita para entender un check (la ventana de «qué califica»). */
export interface CheckInfoView {
  id: string;
  name: string;
  level: QualityLevel;
  /** `code` (lo revisa el código) o `judge` (un juez con IA). */
  kind: string;
  /** La familia del check («Apertura», «Envío»…). */
  family: string;
  /** La etapa del guion donde se mira. */
  stage: string;
  /** Cuándo se califica (si no aplica, el check no cuenta). */
  applies: string;
  /** Qué tiene que pasar para aprobar. */
  rule: string;
}

export interface CheckCatalogView {
  checks: CheckSpecView[];
}

const LEVEL_NAME: Record<QualityLevel, string> = { critico: "crítico", mayor: "mayor", menor: "menor" };

/** Qué hace cada nivel con el veredicto (misma regla que `scorecard/verdict.py`). */
export const LEVEL_HELP: Record<QualityLevel, string> = {
  critico: "Si falla, la conversación reprueba.",
  mayor: "Si falla, la conversación queda en alerta.",
  menor: "No cambia el resultado; sirve para priorizar.",
};

export function levelLabel(level: QualityLevel): string {
  return LEVEL_NAME[level];
}

/** Un check listo para mostrar: su nombre, lo que se esperaba y por qué falló. */
export interface CheckView {
  id: string;
  name: string;
  /** El nivel que contó para el veredicto. */
  level: QualityLevel;
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

function specOf(catalog: CheckCatalogView | undefined, id: string): CheckSpecView | undefined {
  return catalog?.checks.find((c) => c.id === id);
}

export function checkView(catalog: CheckCatalogView | undefined, result: EvalResultView): CheckView {
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
export function turnVerdict(catalog: CheckCatalogView | undefined, results: EvalResultView[]): QualityVerdict {
  const decided = results.filter((r) => r.verdict === "pasa" || r.verdict === "falla");
  if (decided.length === 0) return "SIN_DATOS";
  const levels = decided.filter((r) => r.verdict === "falla").map((r) => r.level ?? specOf(catalog, r.check_id)?.level ?? "menor");
  if (levels.includes("critico")) return "FALLA";
  if (levels.includes("mayor")) return "ALERTA";
  return "PASA";
}

const VERDICT_RANK: Record<QualityVerdict, number> = { FALLA: 0, ALERTA: 1, PASA: 2, SIN_DATOS: 3 };

/** El peor veredicto de varios episodios (FALLA > ALERTA > PASA > SIN_DATOS). */
export function worstVerdict(verdicts: QualityVerdict[]): QualityVerdict {
  return verdicts.reduce<QualityVerdict>((worst, v) => (VERDICT_RANK[v] < VERDICT_RANK[worst] ? v : worst), "SIN_DATOS");
}

/** El cliente sin su número completo: solo los 4 últimos caracteres. */
export function customerLabel(sessionId: string): string {
  return `Cliente ···${sessionId.slice(-4)}`;
}

/** Dólares con coma decimal; `digits` sube la precisión (costo por turno: 4). */
export function formatUsd(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `US$${value.toLocaleString("es-CO", { maximumFractionDigits: digits })}`;
}
