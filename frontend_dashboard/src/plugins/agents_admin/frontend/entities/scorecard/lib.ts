import type {
  CheckDefinition,
  CheckFamily,
  CheckLevel,
  CheckRegistry,
  CheckResult,
  CheckStatus,
  CheckVerdict,
  EpisodeVerdict,
  FailurePoint,
  Fidelity,
  Trajectory,
  TrajectoryTurn,
} from "./model";

/**
 * Lógica pura del scorecard por etapa: etiquetas, orden, colores (tokens del
 * `@theme`) y el modelo de la tira de trayectoria. Sin React ni fetch: lo
 * consumen varias features (tira, panel, matriz, pareto, embudo, tendencia),
 * así que vive en la entity y no se duplica.
 */

// ── Etapas ─────────────────────────────────────────────────────────────────

export const STAGE_ORDER = [
  "descubrimiento",
  "variantes",
  "confirmacion",
  "datos_envio",
  "cierre",
  "postcierre",
  "transversal",
] as const;

const STAGE_LABELS: Record<string, string> = {
  descubrimiento: "descubrimiento",
  variantes: "variantes",
  confirmacion: "confirmación",
  datos_envio: "datos de envío",
  cierre: "cierre",
  postcierre: "post-cierre",
  transversal: "transversal",
};

/** Cada etapa con su token (identidad reforzada SIEMPRE con texto visible). */
const STAGE_COLORS: Record<string, string> = {
  descubrimiento: "var(--color-info)",
  variantes: "var(--color-violet)",
  confirmacion: "var(--color-cyan)",
  datos_envio: "var(--color-yellow)",
  cierre: "var(--color-pink)",
  postcierre: "var(--color-accent)",
  transversal: "var(--color-neutral)",
};

export function stageLabel(stage: string | null | undefined): string {
  if (!stage) return "sin etapa";
  return STAGE_LABELS[stage] ?? stage.replaceAll("_", " ");
}

export function stageColor(stage: string | null | undefined): string {
  return (stage && STAGE_COLORS[stage]) || "var(--color-neutral)";
}

/** Índice de orden de una etapa (desconocidas al final, antes de transversal no). */
export function stageRank(stage: string | null | undefined): number {
  const i = STAGE_ORDER.indexOf((stage ?? "") as (typeof STAGE_ORDER)[number]);
  return i === -1 ? STAGE_ORDER.length : i;
}

// ── Veredictos de episodio ─────────────────────────────────────────────────

export const EPISODE_VERDICT_ORDER: readonly EpisodeVerdict[] = [
  "FALLA",
  "ALERTA",
  "PASA",
  "SIN_DATOS",
];

const EPISODE_VERDICT_LABELS: Record<EpisodeVerdict, string> = {
  FALLA: "Falla",
  ALERTA: "Alerta",
  PASA: "Pasa",
  SIN_DATOS: "Sin datos",
};

const EPISODE_VERDICT_COLORS: Record<EpisodeVerdict, string> = {
  FALLA: "var(--color-red)",
  ALERTA: "var(--color-orange)",
  PASA: "var(--color-green)",
  SIN_DATOS: "var(--color-neutral)",
};

export function episodeVerdictLabel(v: EpisodeVerdict): string {
  return EPISODE_VERDICT_LABELS[v];
}

export function episodeVerdictColor(v: EpisodeVerdict): string {
  return EPISODE_VERDICT_COLORS[v];
}

export function compareEpisodeVerdict(a: EpisodeVerdict, b: EpisodeVerdict): number {
  return EPISODE_VERDICT_ORDER.indexOf(a) - EPISODE_VERDICT_ORDER.indexOf(b);
}

// ── Checks: veredicto, nivel, estado visual ────────────────────────────────

const LEVEL_LABELS: Record<CheckLevel, string> = {
  critico: "crítico",
  mayor: "mayor",
  menor: "menor",
};

export function levelLabel(level: CheckLevel): string {
  return LEVEL_LABELS[level];
}

const CHECK_VERDICT_LABELS: Record<CheckVerdict, string> = {
  pasa: "pasa",
  falla: "falla",
  no_aplica: "no aplica",
  desconocido: "desconocido",
};

export function checkVerdictLabel(v: CheckVerdict): string {
  return CHECK_VERDICT_LABELS[v];
}

export function checkStatus(
  verdict: CheckVerdict | null | undefined,
  level: CheckLevel,
): CheckStatus {
  if (!verdict) return "sin_resultado";
  return verdict === "falla" ? level : verdict;
}

/** Severidad de presentación: fallas primero, luego lo incierto, luego lo sano. */
export const STATUS_ORDER: readonly CheckStatus[] = [
  "critico",
  "mayor",
  "menor",
  "desconocido",
  "pasa",
  "no_aplica",
  "sin_resultado",
];

export function compareStatus(a: CheckStatus, b: CheckStatus): number {
  return STATUS_ORDER.indexOf(a) - STATUS_ORDER.indexOf(b);
}

const STATUS_GLYPHS: Record<CheckStatus, string> = {
  pasa: "✓",
  critico: "✗",
  mayor: "✗",
  menor: "✗",
  no_aplica: "–",
  desconocido: "?",
  sin_resultado: "·",
};

const STATUS_LABELS: Record<CheckStatus, string> = {
  pasa: "pasa",
  critico: "falla crítica",
  mayor: "falla mayor",
  menor: "falla menor",
  no_aplica: "no aplica",
  desconocido: "desconocido",
  sin_resultado: "sin evaluar",
};

const STATUS_COLORS: Record<CheckStatus, string> = {
  pasa: "var(--color-green)",
  critico: "var(--color-red)",
  mayor: "var(--color-orange)",
  menor: "var(--color-yellow)",
  no_aplica: "var(--color-neutral)",
  desconocido: "var(--color-violet)",
  sin_resultado: "var(--color-line-strong)",
};

export function statusGlyph(s: CheckStatus): string {
  return STATUS_GLYPHS[s];
}

export function statusLabel(s: CheckStatus): string {
  return STATUS_LABELS[s];
}

export function statusColor(s: CheckStatus): string {
  return STATUS_COLORS[s];
}

/** Color de un nivel (Pareto, tendencia): mismo token que su falla. */
export function levelColor(level: CheckLevel): string {
  return STATUS_COLORS[level];
}

const FIDELITY_LABELS: Record<Fidelity, string> = {
  trace: "traza completa",
  legacy: "reconstrucción legada — datos parciales",
  empty: "sin datos de trayectoria",
};

export function fidelityLabel(f: Fidelity): string {
  return FIDELITY_LABELS[f];
}

// ── Formatos ───────────────────────────────────────────────────────────────

export function formatCompliance(c: number | null | undefined): string {
  return c === null || c === undefined ? "—" : `${Math.round(c * 100)} %`;
}

export function formatDuration(ms: number): string {
  const totalSec = Math.round(ms / 1000);
  if (totalSec < 60) return `${totalSec} s`;
  const totalMin = Math.floor(totalSec / 60);
  if (totalMin < 60) return `${totalMin} min`;
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

export function failurePointLabel(p: FailurePoint | null | undefined): string | null {
  if (!p) return null;
  return `${p.turn === null ? "sin turno" : `turno ${p.turn}`} · ${p.check_id}`;
}

export function truncate(text: string, max: number): string {
  return text.length > max ? text.slice(0, Math.max(0, max - 1)) + "…" : text;
}

/** `{producto: "cubo-love", color: "Azul"}` → `producto=cubo-love · color=Azul`. */
export function summarizeArgs(args: Record<string, unknown>, max = 80): string {
  const parts = Object.entries(args).map(([k, v]) => {
    const val = typeof v === "string" ? v : JSON.stringify(v);
    return `${k}=${val}`;
  });
  return truncate(parts.join(" · "), max);
}

const SIGNAL_LABELS: Record<string, string> = {
  deferral: "aplazamiento",
  affirmation: "afirmación",
};

export function signalLabel(signal: string): string {
  return SIGNAL_LABELS[signal] ?? signal.replaceAll("_", " ");
}

const TRIGGER_LABELS: Record<string, string> = {
  customer: "cliente",
  ghost: "ghosting",
  handoff: "handoff",
};

export function triggerLabel(trigger: string): string {
  return TRIGGER_LABELS[trigger] ?? trigger;
}

/** `wa_100000000001` + `ep_007` → `wa_…0000001 · ep_007` (id completo va en el title). */
export function episodeLabel(row: { session_id: string; episode_id: string }): string {
  const sid = row.session_id;
  const m = /^([a-z]+_)(.*)$/.exec(sid);
  const prefix = m ? m[1] : "";
  const body = m ? m[2] : sid;
  const short = body.length > 8 ? `${prefix}…${body.slice(-7)}` : sid;
  return row.episode_id ? `${short} · ${row.episode_id}` : short;
}

// ── Agrupación por familia (panel) ─────────────────────────────────────────

export interface FamilyItem {
  checkId: string;
  check: CheckDefinition | null;
  result: CheckResult | null;
  status: CheckStatus;
}

export interface FamilyGroup {
  id: string;
  label: string;
  stage: string;
  items: FamilyItem[];
}

const OTHER_FAMILY: CheckFamily = { id: "__otros__", label: "Otros", stage: "transversal" };

/**
 * Agrupa los resultados por familia en el orden del registro; dentro de cada
 * familia, fallas primero (por severidad) y luego por id. Los checks del
 * registro sin resultado (p. ej. checks de juez con el juez apagado) aparecen
 * como `sin_resultado` salvo `includeUnevaluated: false`.
 */
export function groupResultsByFamily(
  registry: CheckRegistry,
  results: readonly CheckResult[],
  opts: { includeUnevaluated?: boolean } = {},
): FamilyGroup[] {
  const includeUnevaluated = opts.includeUnevaluated ?? true;
  const byCheck = new Map(registry.checks.map((c) => [c.id, c]));
  const byResult = new Map(results.map((r) => [r.check_id, r]));

  const families = [...registry.families];
  const known = new Set(families.map((f) => f.id));
  for (const c of registry.checks) {
    if (!known.has(c.family)) {
      known.add(c.family);
      families.push({ id: c.family, label: c.family_label || c.family, stage: c.stage });
    }
  }

  const buckets = new Map<string, FamilyItem[]>();
  const push = (familyId: string, item: FamilyItem) => {
    const list = buckets.get(familyId) ?? [];
    list.push(item);
    buckets.set(familyId, list);
  };

  for (const c of registry.checks) {
    const r = byResult.get(c.id) ?? null;
    if (!r && !includeUnevaluated) continue;
    push(c.family, {
      checkId: c.id,
      check: c,
      result: r,
      status: checkStatus(r?.verdict, r?.level ?? c.level),
    });
  }
  for (const r of results) {
    if (byCheck.has(r.check_id)) continue;
    push(OTHER_FAMILY.id, {
      checkId: r.check_id,
      check: null,
      result: r,
      status: checkStatus(r.verdict, r.level),
    });
  }

  const groups: FamilyGroup[] = [];
  for (const f of [...families, OTHER_FAMILY]) {
    const items = buckets.get(f.id);
    if (!items?.length) continue;
    items.sort((a, b) => compareStatus(a.status, b.status) || a.checkId.localeCompare(b.checkId));
    groups.push({ id: f.id, label: f.label || f.id, stage: f.stage, items });
  }
  return groups;
}

// ── Tira de trayectoria ────────────────────────────────────────────────────

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
  verdict: CheckVerdict;
  level: CheckLevel;
  status: CheckStatus;
  /** false = el check no trae turno y se ancla al último. */
  anchored: boolean;
}

export interface StripColumn {
  index: number;
  turn: number;
  atMs: number | null;
  trigger: string;
  stage: string | null;
  /** Hueco (ms) desde el turno anterior cuando supera `GAP_THRESHOLD_MS`. */
  gapBeforeMs: number | null;
  lanes: Record<StripEventLaneId, StripChip[]>;
  checks: StripCheck[];
  isFirstFailure: boolean;
  isFirstCritical: boolean;
}

export interface StageBand {
  stage: string | null;
  label: string;
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

/** Huecos entre turnos que la tira señala (30 min). */
export const GAP_THRESHOLD_MS = 30 * 60 * 1000;

function clientChips(t: TrajectoryTurn): StripChip[] {
  const chips: StripChip[] = [];
  const text = t.inbound_text ?? "";
  if (t.trigger === "ghost") {
    chips.push({ kind: "system", text: "silencio del cliente", detail: text || "El cliente dejó de responder", alert: false });
  } else if (t.trigger === "handoff") {
    chips.push({ kind: "handoff", text: text || "handoff", detail: `Handoff: ${text}`, alert: false });
  } else if (text) {
    chips.push({ kind: "customer", text, detail: text, alert: false });
  }
  if (t.signal) {
    const label = signalLabel(t.signal);
    chips.push({ kind: "signal", text: `señal · ${label}`, detail: `Señal del cliente: ${label}`, alert: false });
  }
  return chips;
}

function botChips(t: TrajectoryTurn): StripChip[] {
  const chips: StripChip[] = t.sent_texts.map((s) => ({
    kind: "sent" as const,
    text: s,
    detail: s,
    alert: false,
  }));
  if (t.suppressed_reason) {
    const text = t.llm_text || "(sin texto)";
    chips.push({
      kind: "suppressed",
      text,
      detail: `No enviado (${t.suppressed_reason}): «${text}»`,
      alert: false,
    });
  }
  for (const n of t.discarded_narration) {
    chips.push({
      kind: "discarded",
      text: n,
      detail: `Narración descartada por default-deny (no llegó al cliente): «${n}»`,
      alert: false,
    });
  }
  return chips;
}

function toolChips(t: TrajectoryTurn): StripChip[] {
  return t.tools.map((tool) => {
    const args = summarizeArgs(tool.args);
    const parts = [`${tool.name}(${args})`];
    if (tool.notes.length) parts.push(`notas: ${tool.notes.join(", ")}`);
    if (tool.ok === false) parts.push(`rechazada${tool.error ? `: ${tool.error}` : ""}`);
    else if (tool.error) parts.push(`error: ${tool.error}`);
    return {
      kind: tool.ok === false ? "tool_rejected" : tool.ok === null ? "tool_unknown" : "tool_ok",
      text: tool.name,
      detail: parts.join(" · "),
      alert: false,
    };
  });
}

function stateChips(
  t: TrajectoryTurn,
  prev: TrajectoryTurn | undefined,
  alert: boolean,
): StripChip[] {
  const chips: StripChip[] = [];
  for (const change of t.state.changes) {
    if (!change.tag) continue;
    const extra = [change.source && `origen ${change.source}`, change.reason && `motivo ${change.reason}`]
      .filter(Boolean)
      .join(" · ");
    chips.push({
      kind: "tag",
      text: change.tag,
      detail: `Etiqueta ${change.tag}${extra ? ` · ${extra}` : ""}`,
      alert,
    });
  }
  const route = t.state.route;
  const prevRoute = prev ? prev.state.route : null;
  if (route && prev && route !== prevRoute) {
    chips.push({ kind: "route", text: `ruta ${route}`, detail: `Ruta activa: ${prevRoute ?? "—"} → ${route}`, alert });
  }
  if (t.confirmed === true && prev?.confirmed !== true) {
    chips.push({ kind: "confirmed", text: "confirmado", detail: "Compra confirmada por el cliente", alert: false });
  }
  return chips;
}

function earliest(
  results: readonly CheckResult[],
  turns: ReadonlySet<number>,
  pred: (r: CheckResult) => boolean,
): StripPoint | null {
  let best: CheckResult | null = null;
  for (const r of results) {
    if (r.verdict !== "falla" || r.turn === null || !turns.has(r.turn) || !pred(r)) continue;
    if (
      !best ||
      r.turn < (best.turn as number) ||
      (r.turn === best.turn &&
        (compareStatus(r.level, best.level) || r.check_id.localeCompare(best.check_id)) < 0)
    ) {
      best = r;
    }
  }
  return best ? { turn: best.turn as number, checkId: best.check_id } : null;
}

/**
 * Trayectoria + resultados → modelo de la tira: columnas por turno con carriles,
 * bandas contiguas de etapa, checks anclados (los sin turno van al último) y
 * marcas de primer fallo / primer crítico. `registry` (opcional) aporta nombres
 * de checks y permite marcar en alerta el carril de estado cuando falla un check
 * de la familia `estado` en ese turno. Los `no_aplica` no se dibujan.
 */
export function buildStripModel(
  trajectory: Trajectory,
  results: readonly CheckResult[],
  registry?: CheckRegistry,
): StripModel {
  const turns = [...trajectory.turns].sort((a, b) => a.turn - b.turn);
  if (turns.length === 0) {
    return { columns: [], bands: [], firstFailure: null, firstCritical: null };
  }
  const defs = new Map((registry?.checks ?? []).map((c) => [c.id, c]));
  const turnSet = new Set(turns.map((t) => t.turn));
  const lastTurn = turns[turns.length - 1].turn;

  const checksByTurn = new Map<number, StripCheck[]>();
  for (const r of results) {
    if (r.verdict === "no_aplica") continue;
    const anchored = r.turn !== null && turnSet.has(r.turn);
    const turn = anchored ? (r.turn as number) : lastTurn;
    const list = checksByTurn.get(turn) ?? [];
    list.push({
      checkId: r.check_id,
      name: defs.get(r.check_id)?.name ?? r.check_id,
      verdict: r.verdict,
      level: r.level,
      status: checkStatus(r.verdict, r.level),
      anchored,
    });
    checksByTurn.set(turn, list);
  }

  const firstFailure = earliest(results, turnSet, () => true);
  const firstCritical = earliest(results, turnSet, (r) => r.level === "critico");

  const columns: StripColumn[] = turns.map((t, index) => {
    const prev = index > 0 ? turns[index - 1] : undefined;
    const gap = prev && prev.at_ms !== null && t.at_ms !== null ? t.at_ms - prev.at_ms : null;
    const stateAlert = results.some(
      (r) =>
        r.verdict === "falla" &&
        r.turn === t.turn &&
        defs.get(r.check_id)?.family === "estado",
    );
    const checks = (checksByTurn.get(t.turn) ?? []).sort(
      (a, b) => compareStatus(a.status, b.status) || a.checkId.localeCompare(b.checkId),
    );
    return {
      index,
      turn: t.turn,
      atMs: t.at_ms,
      trigger: t.trigger,
      stage: t.stage_out ?? t.stage_in,
      gapBeforeMs: gap !== null && gap > GAP_THRESHOLD_MS ? gap : null,
      lanes: {
        cliente: clientChips(t),
        bot: botChips(t),
        tools: toolChips(t),
        componentes: t.intents.map((i) => ({ kind: "intent", text: i, detail: `Componente de UI: ${i}`, alert: false })),
        estado: stateChips(t, prev, stateAlert),
        guardas: t.guards.map((g) => ({ kind: "guard", text: g, detail: `Guarda disparada: ${g}`, alert: false })),
      },
      checks,
      isFirstFailure: firstFailure?.turn === t.turn,
      isFirstCritical: firstCritical?.turn === t.turn,
    };
  });

  const bands: StageBand[] = [];
  for (const col of columns) {
    const last = bands[bands.length - 1];
    if (last && last.stage === col.stage) last.to = col.index;
    else bands.push({ stage: col.stage, label: stageLabel(col.stage), from: col.index, to: col.index });
  }

  return { columns, bands, firstFailure, firstCritical };
}
