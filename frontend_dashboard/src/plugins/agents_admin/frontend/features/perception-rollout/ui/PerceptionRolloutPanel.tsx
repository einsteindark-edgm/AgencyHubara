/**
 * Encendido del bot nuevo (capas con clasificador) — plan del laboratorio PR 16.
 *
 * Muestra el modo (y el que de verdad corre bajo el techo de Terraform), el
 * perfil del clasificador, lo medido en sombra y quién hizo el último cambio.
 * Apagar es el interruptor de emergencia: está siempre, aunque no se pueda
 * leer el estado o haya otro cambio en vuelo. Subir de modo se deshabilita si
 * un chequeo falla y dice cuál; canary y encendido llegan a clientes, así que
 * piden confirmar en dos pasos diciendo a cuántas conversaciones llegan. El
 * servidor vuelve a chequear (422 con los que fallan): el panel no decide.
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

const MODES: PerceptionMode[] = ["off", "shadow", "canary", "on"];

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

type Pending = { mode: PerceptionMode; label: string };

const inputStyle: React.CSSProperties = {
  display: "block",
  width: "100%",
  marginTop: 4,
  padding: "6px 8px",
  background: "transparent",
  border: "1px solid var(--color-line-strong)",
  borderRadius: 4,
  color: "var(--fg)",
  fontSize: 12,
};

const lastChangeFormat = new Intl.DateTimeFormat("es-CO", {
  timeZone: "America/Bogota",
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function rank(mode: PerceptionMode): number {
  return MODES.indexOf(mode);
}

function percent(rate: number): string {
  return `${(rate * 100).toFixed(1).replace(".", ",")} %`;
}

function shadowSummary(metrics: Rollout["metrics"]): string {
  if (metrics.turns === 0) return "Sin turnos en sombra todavía";
  const fallbacks = metrics.fallback_rate === null ? "—" : percent(metrics.fallback_rate);
  const p95 = metrics.p95_ms === null ? "—" : `${metrics.p95_ms} ms`;
  return `${metrics.days} días · ${metrics.turns} turnos · caídas ${fallbacks} · p95 ${p95}`;
}

function modeLabel(data: Rollout): string {
  const { mode } = data.state;
  if (rank(mode) > rank(data.ceiling)) {
    return `${MODE_LABEL[mode]} (corre en ${MODE_LABEL[data.ceiling].toLowerCase()} por el techo)`;
  }
  return MODE_LABEL[mode];
}

function reach(canaryPercent: number, numbers: string[]): string {
  const share = `al ${canaryPercent} % de las conversaciones`;
  if (numbers.length === 0) return share;
  return `${share} y a ${numbers.length} ${numbers.length === 1 ? "número" : "números"} de prueba`;
}

function confirmText(mode: PerceptionMode, canaryPercent: number, numbers: string[]): string {
  if (mode === "on") return "El bot nuevo responderá a todas las conversaciones.";
  return `El bot nuevo responderá ${reach(canaryPercent, numbers)}; el resto sigue midiendo en sombra.`;
}

/** Título y líneas de un error: un 422 `not_ready` trae los chequeos que
 * fallan; un 504 del cast (o una red caída) puede haberse aplicado. */
function errorView(error: unknown): { title: string; lines: string[] } {
  if (error instanceof ApiError) {
    if (error.status === 504 || error.status === 0) {
      return { title: "No se sabe si se aplicó:", lines: ["El cambio puede haberse aplicado; releyendo el estado del encendido."] };
    }
    const detail = (error.body as { detail?: unknown } | null)?.detail;
    if (detail && typeof detail === "object") {
      const d = detail as { readiness?: { ok?: boolean; detail?: string }[]; message?: string };
      const failing = (d.readiness ?? []).filter((c) => !c.ok && c.detail).map((c) => c.detail as string);
      if (failing.length > 0) return { title: "No se cambió el modo:", lines: failing };
      if (d.message) return { title: "No se cambió el modo:", lines: [d.message] };
    }
    if (typeof detail === "string") return { title: "No se cambió el modo:", lines: [detail] };
    return { title: "No se cambió el modo:", lines: [`Error ${error.status}`] };
  }
  return { title: "No se sabe si se aplicó:", lines: ["El cambio puede haberse aplicado; releyendo el estado del encendido."] };
}

function ErrorAlert({ error }: { error: unknown }) {
  const view = errorView(error);
  return (
    <div role="alert" style={{ fontSize: 11, color: "var(--color-danger)", marginTop: 8 }}>
      <div>{view.title}</div>
      <ul style={{ margin: "4px 0 0", paddingLeft: 16 }}>
        {view.lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </div>
  );
}

function parseNumbers(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((n) => n.trim())
    .filter(Boolean);
}

/** Apagar tiene su propia mutación: nunca espera a otro cambio en vuelo. */
function TurnOffButton({ turnOff }: { turnOff: ReturnType<typeof useSetPerceptionRollout> }) {
  return (
    <MacButton
      sm
      onClick={() => {
        turnOff.reset();
        turnOff.mutate({ mode: "off" });
      }}
      disabled={turnOff.isPending}
      style={{ color: "var(--color-danger)" }}
    >
      Apagar
    </MacButton>
  );
}

export function PerceptionRolloutPanel() {
  const { data, isLoading, isError } = usePerceptionRollout();
  const setMode = useSetPerceptionRollout();
  const turnOff = useSetPerceptionRollout();
  const [pending, setPending] = useState<Pending | null>(null);
  const [percentDraft, setPercentDraft] = useState<string | null>(null);
  const [numbersDraft, setNumbersDraft] = useState<string | null>(null);

  if (isLoading) return null;
  if (!data) {
    return (
      <Panel title="Bot nuevo">
        <div style={{ fontSize: 11, color: "var(--fg-mute)" }}>
          No se pudo leer el estado del encendido. Apagar sigue disponible.
        </div>
        <div style={{ marginTop: 10 }}>
          <TurnOffButton turnOff={turnOff} />
        </div>
        {turnOff.isError && <ErrorAlert error={turnOff.error} />}
      </Panel>
    );
  }

  const { state } = data;
  const percentValue = percentDraft ?? String(state.canary_percent);
  const numbersValue = numbersDraft ?? state.test_numbers.join(", ");
  const draftPercent = Number.parseInt(percentValue, 10) || 0;
  const draftNumbers = parseNumbers(numbersValue);
  const draftChanged =
    draftPercent !== state.canary_percent || draftNumbers.join(",") !== state.test_numbers.join(",");

  const send = (mode: PerceptionMode) => {
    const change: RolloutChange =
      mode === "canary" ? { mode, canary_percent: draftPercent, test_numbers: draftNumbers } : { mode };
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
  const lastChange =
    state.updated_at_ms !== null
      ? `${lastChangeFormat.format(new Date(state.updated_at_ms))}${state.updated_by ? ` por ${state.updated_by}` : ""}`
      : null;

  return (
    <Panel title="Bot nuevo">
      <div className="form-row">
        <span className="lbl">Modo</span>
        <span className="val">{modeLabel(data)}</span>
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
      {lastChange && (
        <div className="form-row">
          <span className="lbl">Último cambio</span>
          <span className="val">{lastChange}</span>
        </div>
      )}
      {isError && (
        <div style={{ fontSize: 11, color: "var(--color-warn)", marginTop: 4 }}>
          No se pudo actualizar el estado: lo que ves puede estar viejo.
        </div>
      )}

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
      {draftChanged && (
        <div style={{ fontSize: 11, color: "var(--fg-mute)", marginTop: 4 }}>
          {`Guardado: ${reach(state.canary_percent, [...state.test_numbers])}`}
        </div>
      )}

      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>
        <TurnOffButton turnOff={turnOff} />
        {RAISE.map(({ mode, button, confirm }) => (
          <MacButton
            key={mode}
            sm
            ghost
            disabled={setMode.isPending || state.mode === mode || (data.can[mode] ?? []).length > 0}
            onClick={() => (confirm ? setPending({ mode, label: confirm }) : send(mode))}
          >
            {button}
          </MacButton>
        ))}
        {state.mode === "canary" && (
          <MacButton
            sm
            ghost
            disabled={setMode.isPending || !draftChanged}
            onClick={() => setPending({ mode: "canary", label: "Sí, aplicar" })}
          >
            Aplicar
          </MacButton>
        )}
      </div>

      {pending && (
        <div
          role="group"
          aria-label="Confirmar el cambio de modo"
          style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 10 }}
        >
          <p style={{ fontSize: 11, color: "var(--fg-soft)", margin: 0 }}>
            {confirmText(pending.mode, draftPercent, draftNumbers)}
          </p>
          <div style={{ display: "flex", gap: 6 }}>
            <MacButton ghost sm onClick={() => setPending(null)}>
              Volver
            </MacButton>
            <MacButton primary sm disabled={setMode.isPending} onClick={() => send(pending.mode)}>
              {pending.label}
            </MacButton>
          </div>
        </div>
      )}

      {setMode.isError && <ErrorAlert error={setMode.error} />}
      {turnOff.isError && <ErrorAlert error={turnOff.error} />}

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
