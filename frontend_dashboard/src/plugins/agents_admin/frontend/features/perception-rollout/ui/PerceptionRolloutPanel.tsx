/**
 * Bot nuevo (capas con clasificador) — plan del laboratorio PR 16.
 *
 * SOLO LECTURA desde el 2026-10-06 (decisión del operador: «que los botones
 * de la UI no sirvan y todo se haga por comandos, para evitar que alguien
 * jugando dañe producción»). Muestra el modo (y el que de verdad corre bajo
 * el techo de Terraform), el perfil, lo medido en sombra, los números de
 * prueba (tapados), si deciden con Jev, quién hizo el último cambio y qué
 * falta para subir. Para cambiar: el comando (`decisions/control.py`).
 */

import { usePerceptionRollout, type PerceptionMode, type Rollout } from "@plugins/agents_admin/frontend/entities/perception-rollout";
import { Panel } from "@/shared/ui";

import { ByCommandNote } from "./ByCommandNote";

const MODES: PerceptionMode[] = ["off", "shadow", "canary", "on"];

const MODE_LABEL: Record<PerceptionMode, string> = {
  off: "Apagado",
  shadow: "Sombra",
  canary: "Canary",
  on: "Encendido",
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

/** Un número de prueba tapado: solo los últimos cuatro dígitos. */
function masked(sid: string): string {
  const digits = sid.replace(/\D/g, "");
  return digits.length >= 4 ? `···${digits.slice(-4)}` : "···";
}

export function PerceptionRolloutPanel() {
  const { data, isLoading, isError } = usePerceptionRollout();

  if (isLoading) return null;
  if (!data) {
    return (
      <Panel title="Bot nuevo">
        <div style={{ fontSize: 11, color: "var(--fg-mute)" }}>No se pudo leer el estado del bot nuevo.</div>
        <ByCommandNote example="--por <quien> percepcion off" />
      </Panel>
    );
  }

  const { state } = data;
  const above = MODES.filter((mode) => rank(mode) > rank(state.mode) && (data.can[mode] ?? []).length > 0);
  const lastChange =
    state.updated_at_ms !== null
      ? `${lastChangeFormat.format(new Date(state.updated_at_ms))}${state.updated_by ? ` por ${state.updated_by}` : ""}`
      : null;
  const numbers = state.test_numbers.length > 0 ? state.test_numbers.map(masked).join(", ") : "ninguno";

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
      <div className="form-row">
        <span className="lbl">Números de prueba</span>
        <span className="val">{`${numbers} · porcentaje ${state.canary_percent} %`}</span>
      </div>
      <div className="form-row">
        <span className="lbl">Con Jev</span>
        <span className="val">{`Deciden con Jev: ${data.test_numbers_jev ? "sí" : "no"} (solo los números de prueba)`}</span>
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

      {above.length > 0 && (
        <div style={{ fontSize: 11, color: "var(--fg-mute)", marginTop: 10 }}>
          {above.map((mode) => (
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

      <ByCommandNote example="--por <quien> percepcion|numeros|porcentaje|prueba-jev …" />
    </Panel>
  );
}
