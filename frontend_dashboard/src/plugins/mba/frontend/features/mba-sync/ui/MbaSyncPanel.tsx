/**
 * Panel "Sincronizar con Meta" (D2.2): último sync (vault) + revisión del
 * plan (diff de solo lectura contra Meta) + apply con confirmación inline de
 * dos pasos (cero diálogos nativos). El apply viaja con el `fingerprint` del
 * plan revisado: si Meta o el workspace cambiaron entre medio, el backend lo
 * rechaza (`plan_changed`) y acá se vuelve a revisar.
 *
 * Nunca toca `rollout.enabled` ni `ai_audience` (lo garantiza el backend);
 * la allowlist se lista como "fuera de alcance" (D2.3).
 */
import { useReducer } from "react";

import {
  ACTION_LABEL,
  SECTION_LABEL,
  describeBlocker,
  planChanges,
  useApplyMbaSync,
  useMbaSyncPlan,
  useMbaSyncState,
  type MbaSyncOp,
  type MbaSyncOutcome,
  type MbaSyncResult,
} from "@plugins/mba/frontend/entities/mba-sync";
import { ApiError } from "@/shared/api";
import { Icon, MacButton } from "@/shared/ui";

interface Props {
  agentId: string;
}

/**
 * Flujo multi-paso como unión discriminada (CLAUDE.md frontend §estado,
 * regla 3). Solo UI state: el resultado del apply se DERIVA de la mutation
 * (`apply.data`), no se copia acá.
 */
type FlowState = { phase: "idle" } | { phase: "review" } | { phase: "confirm" } | { phase: "done" };

type FlowAction = { type: "review" } | { type: "ask_confirm" } | { type: "cancel" } | { type: "finish" } | { type: "reset" };

function flowReducer(state: FlowState, action: FlowAction): FlowState {
  switch (action.type) {
    case "review":
      return { phase: "review" };
    case "ask_confirm":
      return state.phase === "review" ? { phase: "confirm" } : state;
    case "cancel":
      return { phase: "review" };
    case "finish":
      return { phase: "done" };
    case "reset":
      return { phase: "idle" };
  }
}

const OUTCOME_TEXT: Record<string, string> = {
  plan_changed: "El plan cambió desde que lo revisaste (Meta o el workspace se movieron). Volvé a ver los cambios.",
  blocked: "El sync está bloqueado; nada se aplicó.",
  nothing_to_do: "Meta ya tiene exactamente lo del workspace: nada que aplicar.",
};

function fmtWhen(ms: number): string {
  return new Date(ms).toLocaleString("es-CO", { dateStyle: "medium", timeStyle: "short" });
}

/** El motivo real del API (`{detail: {error, kind, detail}}`), no "API error 503". */
function apiMessage(e: unknown): string {
  if (e instanceof ApiError) {
    const body = e.body as { detail?: unknown } | null | undefined;
    const detail = body && typeof body === "object" ? body.detail : undefined;
    if (detail && typeof detail === "object") {
      const d = detail as { error?: string; kind?: string; detail?: string };
      return [d.error, d.kind, d.detail].filter(Boolean).join(" · ");
    }
    if (typeof detail === "string") return detail;
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

function OpRow({ op }: { op: MbaSyncOp }) {
  const tone = op.action === "delete" || op.action === "replace" ? "var(--color-danger)" : op.action === "create" ? "var(--ok, #16a34a)" : "var(--accent, #2563eb)";
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "baseline", fontSize: 12, padding: "3px 0" }}>
      <span style={{ fontWeight: 700, color: tone, minWidth: 82 }}>{ACTION_LABEL[op.action]}</span>
      <span style={{ color: "var(--fg-mute)", minWidth: 90 }}>{SECTION_LABEL[op.section] ?? op.section}</span>
      <span className="mono" style={{ wordBreak: "break-word" }}>{op.label}</span>
      {op.reason && <span style={{ color: "var(--fg-mute)", fontSize: 11 }}>({op.reason})</span>}
    </div>
  );
}

function ResultRow({ r }: { r: MbaSyncResult }) {
  const text = r.ok ? "ok" : r.skipped ? `omitido (${r.skipped})` : `falló: ${r.error?.kind ?? "?"} ${r.error?.detail ?? ""}`;
  return (
    <div style={{ display: "flex", gap: 8, fontSize: 12, padding: "3px 0" }}>
      <span style={{ minWidth: 82, color: r.ok ? "var(--ok, #16a34a)" : "var(--color-danger)", fontWeight: 700 }}>{text}</span>
      <span style={{ color: "var(--fg-mute)", minWidth: 90 }}>{SECTION_LABEL[r.section] ?? r.section}</span>
      <span className="mono">{r.label}</span>
    </div>
  );
}

export function MbaSyncPanel({ agentId }: Props) {
  const [flow, dispatch] = useReducer(flowReducer, { phase: "idle" });
  const state = useMbaSyncState(agentId);
  const reviewing = flow.phase === "review" || flow.phase === "confirm";
  const plan = useMbaSyncPlan(agentId, reviewing);
  const apply = useApplyMbaSync(agentId);
  const outcome: MbaSyncOutcome | undefined = apply.data;

  const last = state.data?.state?.last_apply ?? null;
  const attempt = state.data?.state?.last_attempt ?? null;
  const changes = plan.data ? planChanges(plan.data) : [];
  const noops = plan.data?.counts.noop ?? 0;
  const skips = plan.data?.counts.skip ?? 0;
  const blocked = plan.data?.blocked ?? [];
  const busy = apply.isPending;

  const onApply = () => {
    if (!plan.data || busy) return;
    apply.mutate({ fingerprint: plan.data.fingerprint }, { onSuccess: () => dispatch({ type: "finish" }) });
  };
  const review = () => {
    apply.reset();
    dispatch({ type: "review" });
  };

  return (
    <section className="panel" style={{ marginBottom: 14 }}>
      <div className="panel-h" style={{ cursor: "default" }}>
        <Icon.workflow /> Sincronizar con Meta
      </div>
      <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ fontSize: 12, color: "var(--fg-mute)" }}>
          {state.isError ? (
            "No se pudo leer el estado del último sync."
          ) : last ? (
            <>
              <b>Último sync</b> {fmtWhen(last.at_ms)} · {last.status} · {last.counts.ok} de {last.counts.changes} cambios aplicados
              {last.counts.failed > 0 && <> · {last.counts.failed} fallidos</>}
            </>
          ) : (
            <>
              <b>Nunca sincronizado.</b> El primer sync crea todo lo del workspace en Meta.
            </>
          )}
          {attempt && attempt.reason === "blocked" && (
            <div style={{ marginTop: 4 }}>Último intento {fmtWhen(attempt.at_ms)}: bloqueado.</div>
          )}
          <div style={{ marginTop: 4 }}>
            Nunca toca <code>rollout.enabled</code> ni <code>ai_audience</code>; la allowlist se maneja aparte.
          </div>
        </div>

        {flow.phase === "idle" && (
          <div>
            <MacButton sm onClick={review}>
              Ver cambios
            </MacButton>
          </div>
        )}

        {reviewing && plan.isLoading && (
          <div role="status" style={{ fontSize: 12, color: "var(--fg-mute)" }}>Leyendo el estado en Meta…</div>
        )}

        {reviewing && plan.isError && (
          <div role="alert" style={errStyle}>
            No se pudo leer el estado en Meta: {apiMessage(plan.error)}
            <div style={{ marginTop: 6 }}>
              <MacButton sm onClick={() => dispatch({ type: "reset" })}>Cerrar</MacButton>
            </div>
          </div>
        )}

        {reviewing && plan.data && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ fontSize: 12 }}>
              <b>{changes.length} cambios</b> · {noops} sin cambios · {skips} fuera de alcance
            </div>
            {blocked.length > 0 && (
              <div role="alert" style={warnStyle}>
                <b>Bloqueado: no se aplica nada hasta resolver esto.</b>
                {blocked.map((b) => (
                  <div key={b} className="mono" style={{ marginTop: 3 }}>{describeBlocker(b)}</div>
                ))}
              </div>
            )}
            {changes.length > 0 && (
              <div style={{ borderLeft: "2px solid var(--border, rgba(127,127,127,0.3))", paddingLeft: 10 }}>
                {changes.map((op) => (
                  <OpRow key={`${op.section}:${op.label}:${op.action}`} op={op} />
                ))}
              </div>
            )}
            {apply.isError && (
              <div role="alert" style={errStyle}>{apiMessage(apply.error)}</div>
            )}
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              {blocked.length === 0 && changes.length > 0 && flow.phase === "review" && (
                <MacButton sm primary onClick={() => dispatch({ type: "ask_confirm" })}>
                  Aplicar {changes.length} cambios
                </MacButton>
              )}
              {flow.phase === "confirm" && (
                <>
                  <span role="status" style={{ fontSize: 12 }}>
                    {busy ? "Enviando a Meta…" : `¿Enviar estos ${changes.length} cambios a Meta ahora?`}
                  </span>
                  <MacButton sm primary disabled={busy} onClick={onApply}>
                    {busy ? "Enviando…" : "Confirmar envío a Meta"}
                  </MacButton>
                  <MacButton sm disabled={busy} onClick={() => dispatch({ type: "cancel" })}>Cancelar</MacButton>
                </>
              )}
              {flow.phase === "review" && (
                <MacButton sm ghost onClick={() => dispatch({ type: "reset" })}>Cerrar</MacButton>
              )}
            </div>
          </div>
        )}

        {flow.phase === "done" && outcome && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {outcome.applied ? (
              <div style={{ fontSize: 12 }}>
                <b>Aplicado: {outcome.results.filter((r) => r.ok).length} de {outcome.results.length}</b> · estado {outcome.status}
              </div>
            ) : (
              <div role="alert" style={warnStyle}>{OUTCOME_TEXT[outcome.reason] ?? outcome.reason}</div>
            )}
            {outcome.blocked.length > 0 && (
              <div>
                {outcome.blocked.map((b) => (
                  <div key={b} className="mono" style={{ fontSize: 12 }}>{describeBlocker(b)}</div>
                ))}
              </div>
            )}
            {outcome.results.length > 0 && (
              <div style={{ borderLeft: "2px solid var(--border, rgba(127,127,127,0.3))", paddingLeft: 10 }}>
                {outcome.results.map((r) => (
                  <ResultRow key={`${r.section}:${r.label}:${r.action}`} r={r} />
                ))}
              </div>
            )}
            <div>
              <MacButton sm onClick={review}>Ver cambios</MacButton>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

const warnStyle: React.CSSProperties = {
  padding: "0.4rem 0.55rem",
  borderRadius: 6,
  background: "rgba(255,180,60,0.14)",
  border: "1px solid rgba(255,180,60,0.4)",
  color: "var(--color-warning, #d97706)",
  fontSize: 12,
  lineHeight: 1.35,
};
const errStyle: React.CSSProperties = {
  padding: "0.4rem 0.55rem",
  borderRadius: 6,
  background: "rgba(255,114,105,0.14)",
  border: "1px solid rgba(255,114,105,0.4)",
  color: "var(--color-danger)",
  fontSize: 12,
  lineHeight: 1.35,
};
