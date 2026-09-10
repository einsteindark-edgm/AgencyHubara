/**
 * Panel "Rollout en Meta" (D2.3): quién puede hablar con Meta Business Agent.
 *
 *   - Estado: MBA encendido/apagado en Meta, audiencia, allowlist (con la
 *     marca de si cada teléfono está en la lista cerrada de Hubara).
 *   - Readiness: los chequeos que el backend exige para encender.
 *   - Acciones: agregar/quitar teléfonos, audiencia, encender (dos pasos +
 *     `confirm`) y apagar (kill switch: un click, sin confirmación).
 *   - Drift: con MBA encendido, un chequeo que dejó de cumplirse (teléfono
 *     agregado en Business Manager, lista de Hubara reducida…) es un alert
 *     rojo pegado al kill switch.
 *
 * La política vive en el backend (`domain/rollout_policy.py`); acá solo se
 * refleja: sin readiness no hay botón de encender, EVERYONE no se ofrece con
 * el knob apagado. Cero diálogos nativos.
 */
import { useReducer, useState } from "react";

import {
  CHECK_LABEL,
  REASON_TEXT,
  describeReason,
  pendingChecks,
  useAddRolloutPhone,
  useMbaRollout,
  useRemoveRolloutPhone,
  useSetRolloutAudience,
  useSetRolloutEnabled,
  type MbaRolloutOutcome,
} from "@plugins/mba/frontend/entities/mba-rollout";
import { ApiError } from "@/shared/api";
import { Icon, MacButton } from "@/shared/ui";

interface Props {
  agentId: string;
}

/** Qué confirmación de dos pasos está abierta (una a la vez). */
type Pending = { kind: "none" } | { kind: "enable" } | { kind: "everyone" } | { kind: "remove"; entryId: string };
type PendingAction = { type: "open"; pending: Pending } | { type: "close" };

function pendingReducer(_state: Pending, action: PendingAction): Pending {
  return action.type === "open" ? action.pending : { kind: "none" };
}

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

function fmtWhen(ms: number): string {
  return new Date(ms).toLocaleString("es-CO", { dateStyle: "short", timeStyle: "short" });
}

function Feedback({ outcome, error }: { outcome: MbaRolloutOutcome | undefined; error: unknown }) {
  if (error) {
    return <div role="alert" style={errStyle}>{apiMessage(error)}</div>;
  }
  if (outcome && !outcome.applied) {
    return <div role="alert" style={warnStyle}>{describeReason(outcome)}</div>;
  }
  return null;
}

export function MbaRolloutPanel({ agentId }: Props) {
  const status = useMbaRollout(agentId);
  const add = useAddRolloutPhone(agentId);
  const remove = useRemoveRolloutPhone(agentId);
  const setAudience = useSetRolloutAudience(agentId);
  const setEnabled = useSetRolloutEnabled(agentId);
  const [pending, dispatch] = useReducer(pendingReducer, { kind: "none" });
  const [phone, setPhone] = useState("");

  const data = status.data;
  const busy = add.isPending || remove.isPending || setAudience.isPending || setEnabled.isPending;
  const close = () => dispatch({ type: "close" });

  const onEnable = () => {
    setEnabled.mutate({ enabled: true, confirm: true }, { onSettled: close });
  };
  const onDisable = () => {
    setEnabled.mutate({ enabled: false });
  };
  const onEveryone = () => {
    setAudience.mutate({ audience: "EVERYONE", confirm: true }, { onSettled: close });
  };
  const onAllowlistedOnly = () => {
    setAudience.mutate({ audience: "ALLOWLISTED_ONLY", confirm: false });
  };
  const onAdd = () => {
    const value = phone.trim();
    if (!value || busy) return;
    add.mutate({ phone: value }, { onSuccess: (out) => out.applied && setPhone("") });
  };
  const onRemove = (entryId: string) => {
    remove.mutate({ entryId }, { onSettled: close });
  };

  return (
    <section className="panel" style={{ marginBottom: 14 }}>
      <div className="panel-h" style={{ cursor: "default" }}>
        <Icon.shield /> Rollout en Meta
      </div>
      <div className="panel-b" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {status.isLoading && (
          <div role="status" style={{ fontSize: 12, color: "var(--fg-mute)" }}>Leyendo el rollout en Meta…</div>
        )}
        {status.isError && (
          <div role="alert" style={errStyle}>No se pudo leer el rollout en Meta: {apiMessage(status.error)}</div>
        )}

        {data && (
          <>
            {/* ── Estado + encender/apagar ─────────────────────────────── */}
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div style={{ fontSize: 13, fontWeight: 700 }}>
                {data.rollout_enabled === null
                  ? "Sin settings en Meta (¿onboardeado?)"
                  : data.rollout_enabled
                    ? "MBA encendido en Meta"
                    : "MBA apagado en Meta"}
                {data.entity_id === null && <span style={{ fontWeight: 400, color: "var(--fg-mute)" }}> · sin entity_id</span>}
              </div>
              <div style={{ fontSize: 12, color: "var(--fg-mute)" }}>
                Encender exige los 6 chequeos y una confirmación. Apagar es el kill switch: siempre disponible.
              </div>
              {data.rollout_enabled && data.drift.length > 0 && (
                <div role="alert" style={errStyle}>
                  <b>MBA está encendido y {data.drift.length} chequeo{data.drift.length === 1 ? "" : "s"} dejaron de cumplirse:</b>{" "}
                  {data.drift.map((code) => CHECK_LABEL[code] ?? code).join(" · ")}. Si no es intencional, apagá MBA ahora.
                </div>
              )}
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
                {data.checks.map((c) => (
                  <li key={c.code} style={{ color: c.ok ? "var(--ok, #16a34a)" : "var(--color-warning, #d97706)" }}>
                    {c.ok ? "✓" : "✗"} {CHECK_LABEL[c.code] ?? c.code}
                    {!c.ok && c.detail && <span style={{ color: "var(--fg-mute)" }}> — {c.detail}</span>}
                  </li>
                ))}
              </ul>
              {!data.can_enable && !data.rollout_enabled && (
                <div style={{ fontSize: 12, color: "var(--fg-mute)" }}>
                  {pendingChecks(data).length} de {data.checks.length} chequeos pendientes: no se puede encender todavía.
                </div>
              )}
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                {data.rollout_enabled ? (
                  <MacButton sm danger disabled={busy} onClick={onDisable}>Apagar MBA</MacButton>
                ) : data.can_enable && pending.kind !== "enable" ? (
                  <MacButton sm primary disabled={busy} onClick={() => dispatch({ type: "open", pending: { kind: "enable" } })}>
                    Encender MBA
                  </MacButton>
                ) : null}
                {pending.kind === "enable" && data.can_enable && (
                  <>
                    <span role="status" style={{ fontSize: 12 }}>
                      MBA va a responderle a los {data.allowlist.length} teléfono(s) de la allowlist. ¿Seguro?
                    </span>
                    <MacButton sm primary disabled={busy} onClick={onEnable}>Confirmar: encender para la allowlist</MacButton>
                    <MacButton sm disabled={busy} onClick={close}>Cancelar</MacButton>
                  </>
                )}
              </div>
              <Feedback outcome={setEnabled.data} error={setEnabled.error} />
              {data.history.length > 0 && (
                <div style={{ fontSize: 11, color: "var(--fg-mute)" }}>
                  <b>Últimos cambios</b>
                  {data.history.slice(-5).reverse().map((h) => (
                    <div key={`${h.at_ms}:${h.action}:${h.value}`} className="mono">
                      {fmtWhen(h.at_ms)} · {h.action} → {h.value} · {h.ok ? "ok" : `falló${h.error ? `: ${h.error.kind} ${h.error.detail}` : ""}`}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* ── Audiencia ────────────────────────────────────────────── */}
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div style={{ fontSize: 12 }}>
                <b>Audiencia:</b> <span className="mono">{data.ai_audience ?? "desconocida"}</span>
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                {data.ai_audience !== "ALLOWLISTED_ONLY" && (
                  <MacButton sm disabled={busy} onClick={onAllowlistedOnly}>Volver a ALLOWLISTED_ONLY</MacButton>
                )}
                {data.everyone_allowed ? (
                  data.ai_audience !== "EVERYONE" && pending.kind !== "everyone" ? (
                    <MacButton sm danger disabled={busy} onClick={() => dispatch({ type: "open", pending: { kind: "everyone" } })}>
                      Abrir a EVERYONE
                    </MacButton>
                  ) : null
                ) : (
                  <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>{REASON_TEXT.everyone_not_allowed}</span>
                )}
                {pending.kind === "everyone" && (
                  <>
                    <span role="status" style={{ fontSize: 12 }}>
                      EVERYONE: cualquier cliente que escriba habla con MBA y Meta factura por tokens. ¿Seguro?
                    </span>
                    <MacButton sm danger disabled={busy} onClick={onEveryone}>Confirmar: EVERYONE</MacButton>
                    <MacButton sm disabled={busy} onClick={close}>Cancelar</MacButton>
                  </>
                )}
              </div>
              <Feedback outcome={setAudience.data} error={setAudience.error} />
            </div>

            {/* ── Allowlist ────────────────────────────────────────────── */}
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div style={{ fontSize: 12 }}>
                <b>Allowlist en Meta</b> · {data.allowlist.length} teléfono(s). Solo se aceptan teléfonos que ya estén en la lista cerrada de Hubara.
              </div>
              {data.allowlist.map((e) => (
                <div key={e.id} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12, flexWrap: "wrap" }}>
                  <span className="mono">{e.phone}</span>
                  {!e.in_hubara && (
                    <span style={{ color: "var(--color-warning, #d97706)" }}>fuera de la lista cerrada de Hubara</span>
                  )}
                  {pending.kind === "remove" && pending.entryId === e.id ? (
                    <>
                      <MacButton sm danger disabled={busy} onClick={() => onRemove(e.id)}>Confirmar quitar</MacButton>
                      <MacButton sm disabled={busy} onClick={close}>Cancelar</MacButton>
                    </>
                  ) : (
                    <MacButton sm ghost disabled={busy} onClick={() => dispatch({ type: "open", pending: { kind: "remove", entryId: e.id } })}>
                      Quitar
                    </MacButton>
                  )}
                </div>
              ))}
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <input
                  type="tel"
                  placeholder="+573001234567"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && onAdd()}
                  aria-label="Teléfono E.164 para la allowlist"
                  style={inputStyle}
                />
                <MacButton sm disabled={busy || !phone.trim()} onClick={onAdd}>Agregar</MacButton>
              </div>
              <Feedback outcome={add.data} error={add.error} />
              <Feedback outcome={remove.data} error={remove.error} />
            </div>
          </>
        )}
      </div>
    </section>
  );
}

const inputStyle: React.CSSProperties = {
  padding: "0.3rem 0.5rem",
  borderRadius: 6,
  border: "1px solid var(--border, rgba(127,127,127,0.3))",
  background: "var(--bg, transparent)",
  color: "inherit",
  fontSize: 12,
  width: 180,
};
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
