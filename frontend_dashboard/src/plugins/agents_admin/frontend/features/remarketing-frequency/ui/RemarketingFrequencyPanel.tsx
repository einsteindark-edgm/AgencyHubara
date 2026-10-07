/**
 * Agents → Remarketing → Frecuencia: cuántos toques máximo hace el bot de
 * remarketing por cada vez que el cliente deja de contestar.
 *
 * A diferencia del bot nuevo (solo por comando), este ajuste se EDITA desde el
 * dashboard (operador, 2026-10-07: «dashboard editable, solo cantidad, aplica
 * desde ya»). Solo la cantidad: los huecos entre toques (+2 h, +4 h, +8 h,
 * +14 h, +20 h) no se tocan. El techo lo fija Terraform y no se puede superar.
 * Aplica desde ya, así que BAJAR pide confirmar (quien ya recibió más toques
 * queda «Sin respuesta»); subir es directo.
 */

import { useState } from "react";

import {
  useRemarketingFrequency,
  useSetRemarketingFrequency,
  type LadderStep,
} from "@plugins/agents_admin/frontend/entities/remarketing-frequency";
import { Panel } from "@/shared/ui";

const HOUR_MS = 3_600_000;

const lastChangeFormat = new Intl.DateTimeFormat("es-CO", {
  timeZone: "America/Bogota",
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function hours(ms: number): string {
  const h = ms / HOUR_MS;
  return `${Number.isInteger(h) ? h : h.toFixed(1).replace(".", ",")} h`;
}

function lowerWarning(next: number): string {
  if (next === 0) {
    return "Con 0 el bot no hará remarketing: no sale ningún toque y quien ya recibió alguno queda como «Sin respuesta».";
  }
  return `Bajar a ${next}: quien ya recibió más de ${next} ${next === 1 ? "toque" : "toques"} queda como «Sin respuesta» y no se le escribe más.`;
}

export function RemarketingFrequencyPanel() {
  const { data, isLoading } = useRemarketingFrequency();
  const save = useSetRemarketingFrequency();
  // UI state local y colocado: la baja que espera confirmación.
  const [pendingLower, setPendingLower] = useState<number | null>(null);

  if (isLoading) return null;
  if (!data) {
    return (
      <Panel title="Frecuencia del remarketing">
        <div style={{ fontSize: 11, color: "var(--fg-mute)" }}>
          No se pudo leer la frecuencia del remarketing.
        </div>
      </Panel>
    );
  }

  const top = Math.max(data.ceiling, data.ladder.length);
  const options = Array.from({ length: top + 1 }, (_, n) => n);
  const lastChange =
    data.updated_at_ms !== null
      ? `${lastChangeFormat.format(new Date(data.updated_at_ms))}${data.updated_by ? ` por ${data.updated_by}` : ""}`
      : null;

  const choose = (n: number) => {
    save.reset();
    if (n === data.max_touches) {
      setPendingLower(null);
    } else if (n < data.max_touches) {
      setPendingLower(n);
    } else {
      setPendingLower(null);
      save.mutate(n);
    }
  };

  const confirmLower = () => {
    if (pendingLower === null) return;
    save.mutate(pendingLower, { onSuccess: () => setPendingLower(null) });
  };

  return (
    <Panel title="Frecuencia del remarketing">
      <div style={{ fontSize: 11, color: "var(--fg-mute)", marginBottom: 8 }}>
        Cuántos toques hace el bot, como máximo, cada vez que el cliente deja de
        contestar. Aplica desde ya.
      </div>

      <div className="form-row">
        <span className="lbl">Toques máximo</span>
        <div role="radiogroup" aria-label="Toques máximo" style={{ display: "flex", gap: 6 }}>
          {options.map((n) => (
            <button
              key={n}
              type="button"
              role="radio"
              aria-checked={n === data.max_touches}
              disabled={n > data.ceiling || save.isPending}
              className={"sub-tab" + (n === data.max_touches ? " on" : "")}
              onClick={() => choose(n)}
            >
              {n}
            </button>
          ))}
        </div>
      </div>
      <div className="form-row">
        <span className="lbl">Techo</span>
        <span className="val">{`Techo de Terraform: ${data.ceiling}`}</span>
      </div>
      {lastChange && (
        <div className="form-row">
          <span className="lbl">Último cambio</span>
          <span className="val">{lastChange}</span>
        </div>
      )}

      {pendingLower !== null && (
        <div style={{ fontSize: 11, marginTop: 10 }}>
          <div>{lowerWarning(pendingLower)}</div>
          <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
            <button type="button" className="sub-tab on" disabled={save.isPending} onClick={confirmLower}>
              Confirmar
            </button>
            <button
              type="button"
              className="sub-tab"
              disabled={save.isPending}
              onClick={() => setPendingLower(null)}
            >
              Cancelar
            </button>
          </div>
        </div>
      )}

      {save.isError && (
        <div role="alert" style={{ fontSize: 11, color: "var(--color-warn)", marginTop: 8 }}>
          No se pudo guardar el cambio: la frecuencia sigue como estaba.
        </div>
      )}

      <div style={{ marginTop: 12 }}>
        <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4 }}>
          Cuándo sale cada toque (desde el último mensaje del cliente)
        </div>
        {data.ladder.map((step: LadderStep) => {
          const active = step.touch <= data.max_touches;
          return (
            <div
              key={step.touch}
              className="form-row"
              style={{ opacity: active ? 1 : 0.5 }}
            >
              <span className="lbl">{`Toque ${step.touch}`}</span>
              <span className="val">{hours(step.after_ms)}</span>
              {!active && <span className="val" style={{ color: "var(--fg-mute)" }}>no se envía</span>}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}
