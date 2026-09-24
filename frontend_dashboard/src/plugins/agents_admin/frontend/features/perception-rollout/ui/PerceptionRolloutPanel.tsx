/**
 * Encendido del bot nuevo (capas con clasificador) — plan del laboratorio PR 16.
 *
 * Muestra el modo, el techo que fija Terraform, el perfil del clasificador y
 * lo medido en sombra. Apagar es inmediato y siempre está (interruptor de
 * emergencia). Subir de modo se deshabilita si un chequeo falla y dice cuál;
 * canary y encendido llegan a clientes, así que piden confirmar en dos pasos.
 * El servidor vuelve a chequear (422 con los que fallan): el panel no decide.
 */

import { useState } from "react";

import {
  usePerceptionRollout,
  useSetPerceptionRollout,
  type PerceptionMode,
  type Rollout,
  type RolloutChange,
} from "@plugins/agents_admin/frontend/entities/perception-rollout";
import { ApiError } from "@/shared/sdk";
import { MacButton, Panel } from "@/shared/ui";

const MODE_LABEL: Record<PerceptionMode, string> = {
  off: "Apagado",
  shadow: "Sombra",
  canary: "Canary",
  on: "Encendido",
};

const RAISE: { mode: PerceptionMode; button: string; confirm: string | null }[] = [
  { mode: "shadow", button: "Pasar a sombra", confirm: null },
  { mode: "canary", button: "Canary", confirm: "Sí, pasar a canary" },
  { mode: "on", button: "Encender", confirm: "Sí, encender" },
];

const CONFIRM_TEXT: Partial<Record<PerceptionMode, string>> = {
  canary: "El bot nuevo responderá a los números de prueba y a un porcentaje de conversaciones.",
  on: "El bot nuevo responderá a todas las conversaciones.",
};

const inputStyle: React.CSSProperties = {
  display: "block",
  width: "100%",
  marginTop: 4,
  padding: "6px 8px",
  background: "var(--color-surface-muted, transparent)",
  border: "1px solid var(--color-border)",
  borderRadius: 4,
  color: "var(--fg)",
  fontSize: 12,
};

function percent(rate: number): string {
  return `${(rate * 100).toFixed(1).replace(".", ",")} %`;
}

function shadowSummary(metrics: Rollout["metrics"]): string {
  if (metrics.turns === 0) return "Sin turnos en sombra todavía";
  const fallbacks = metrics.fallback_rate === null ? "—" : percent(metrics.fallback_rate);
  const p95 = metrics.p95_ms === null ? "—" : `${metrics.p95_ms} ms`;
  return `${metrics.days} días · ${metrics.turns} turnos · caídas ${fallbacks} · p95 ${p95}`;
}

/** Detalle de los chequeos que fallan en un 422 `not_ready`, o el mensaje del error. */
function errorLines(error: unknown): string[] {
  if (error instanceof ApiError) {
    const detail = (error.body as { detail?: unknown } | null)?.detail;
    if (detail && typeof detail === "object") {
      const d = detail as { readiness?: { ok?: boolean; detail?: string }[]; message?: string };
      const failing = (d.readiness ?? []).filter((c) => !c.ok && c.detail).map((c) => c.detail as string);
      if (failing.length > 0) return failing;
      if (d.message) return [d.message];
    }
    if (typeof detail === "string") return [detail];
    return [`Error ${error.status}`];
  }
  return [error instanceof Error ? error.message : "No se pudo cambiar el modo"];
}

function parseNumbers(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((n) => n.trim())
    .filter(Boolean);
}

export function PerceptionRolloutPanel() {
  const { data, isLoading, isError } = usePerceptionRollout();
  const setMode = useSetPerceptionRollout();
  const [pending, setPending] = useState<PerceptionMode | null>(null);
  const [percentDraft, setPercentDraft] = useState<string | null>(null);
  const [numbersDraft, setNumbersDraft] = useState<string | null>(null);

  if (isLoading) return null;
  if (isError || !data) {
    return (
      <Panel title="Bot nuevo">
        <div style={{ fontSize: 11, color: "var(--fg-mute)" }}>No se pudo leer el estado del encendido.</div>
      </Panel>
    );
  }

  const { state } = data;
  const percentValue = percentDraft ?? String(state.canary_percent);
  const numbersValue = numbersDraft ?? state.test_numbers.join(", ");

  const send = (mode: PerceptionMode) => {
    const change: RolloutChange =
      mode === "canary"
        ? { mode, canary_percent: Number.parseInt(percentValue, 10) || 0, test_numbers: parseNumbers(numbersValue) }
        : { mode };
    setMode.reset();
    setMode.mutate(change, {
      onSuccess: () => {
        setPending(null);
        setPercentDraft(null);
        setNumbersDraft(null);
      },
    });
  };

  const blocked = RAISE.filter(({ mode }) => (data.can[mode] ?? []).length > 0 && state.mode !== mode);
  const confirmStep = RAISE.find((r) => r.mode === pending);

  return (
    <Panel title="Bot nuevo">
      <div className="form-row">
        <span className="lbl">Modo</span>
        <span className="val">{MODE_LABEL[state.mode]}</span>
      </div>
      <div className="form-row">
        <span className="lbl">Techo (Terraform)</span>
        <span className="val mono">{data.ceiling}</span>
      </div>
      <div className="form-row">
        <span className="lbl">Perfil</span>
        <span className="val mono">{data.profile || "—"}</span>
      </div>
      <div className="form-row">
        <span className="lbl">Medido en sombra</span>
        <span className="val">{shadowSummary(data.metrics)}</span>
      </div>

      <label style={{ display: "block", fontSize: 11, color: "var(--fg-soft)", marginTop: 8 }}>
        Porcentaje canary
        <input
          type="number"
          min={0}
          max={100}
          value={percentValue}
          onChange={(e) => setPercentDraft(e.target.value)}
          style={inputStyle}
        />
      </label>
      <label style={{ display: "block", fontSize: 11, color: "var(--fg-soft)", marginTop: 8 }}>
        Números de prueba
        <input
          placeholder="wa_573001234567, …"
          value={numbersValue}
          onChange={(e) => setNumbersDraft(e.target.value)}
          style={inputStyle}
        />
      </label>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>
        <MacButton
          sm
          onClick={() => send("off")}
          disabled={setMode.isPending}
          style={{ color: "var(--color-danger)" }}
        >
          Apagar
        </MacButton>
        {RAISE.map(({ mode, button, confirm }) => (
          <MacButton
            key={mode}
            sm
            ghost
            disabled={setMode.isPending || state.mode === mode || (data.can[mode] ?? []).length > 0}
            onClick={() => (confirm ? setPending(mode) : send(mode))}
          >
            {button}
          </MacButton>
        ))}
      </div>

      {confirmStep?.confirm && (
        <div
          role="group"
          aria-label="Confirmar el cambio de modo"
          style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 10 }}
        >
          <p style={{ fontSize: 11, color: "var(--fg-soft)", margin: 0 }}>{CONFIRM_TEXT[confirmStep.mode]}</p>
          <div style={{ display: "flex", gap: 6 }}>
            <MacButton ghost sm onClick={() => setPending(null)}>
              Volver
            </MacButton>
            <MacButton primary sm disabled={setMode.isPending} onClick={() => send(confirmStep.mode)}>
              {confirmStep.confirm}
            </MacButton>
          </div>
        </div>
      )}

      {setMode.isError && (
        <div role="alert" style={{ fontSize: 11, color: "var(--color-danger)", marginTop: 8 }}>
          <div>No se cambió el modo:</div>
          <ul style={{ margin: "4px 0 0", paddingLeft: 16 }}>
            {errorLines(setMode.error).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      )}

      {blocked.length > 0 && (
        <div style={{ fontSize: 11, color: "var(--fg-mute)", marginTop: 10 }}>
          {blocked.map(({ mode }) => (
            <div key={mode} style={{ marginTop: 4 }}>
              <div style={{ fontWeight: 600 }}>{`Para ${MODE_LABEL[mode].toLowerCase()} falta:`}</div>
              <ul style={{ margin: "2px 0 0", paddingLeft: 16 }}>
                {(data.readiness[mode] ?? [])
                  .filter((c) => !c.ok)
                  .map((c) => (
                    <li key={c.code}>{c.detail || c.code}</li>
                  ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
