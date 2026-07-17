/**
 * Paso 6 — Enviar: tabs "Ahora" / "Programar".
 *  - Ahora: confirmación INLINE de dos pasos (regla #6: cero window.confirm)
 *    con N destinatarios y costo explícito antes de disparar el workflow.
 *  - Programar: fecha + hora → schedule_at_ms (valida futuro; el backend
 *    re-valida con 422).
 */

import { useState } from "react";

import {
  useSendCampaign,
  type Campaign,
} from "@plugins/marketing/frontend/entities/campaign";
import {
  apiErrorDetail,
  fmtCop,
  fmtN,
  fmtUsdMicros,
  usdMicrosToCop,
} from "@plugins/marketing/frontend/lib/format";

interface Props {
  campaign: Campaign;
  editable: boolean;
  canSend: boolean;
  recipients: number;
  totalCostMicros: number;
}

export function SendStep({
  campaign,
  editable,
  canSend,
  recipients,
  totalCostMicros,
}: Props) {
  const send = useSendCampaign(campaign.id);
  const [mode, setMode] = useState<"now" | "schedule">("now");
  const [armed, setArmed] = useState(false);
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [scheduleHint, setScheduleHint] = useState<string | null>(null);

  const cost = fmtUsdMicros(totalCostMicros);
  const costCop = fmtCop(usdMicrosToCop(totalCostMicros));
  const disabled = !editable || !canSend || send.isPending;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-1 rounded-md bg-white/[0.04] p-0.5 self-start">
        {(
          [
            { key: "now", label: "Ahora" },
            { key: "schedule", label: "Programar" },
          ] as const
        ).map((t) => (
          <button
            key={t.key}
            type="button"
            aria-pressed={mode === t.key}
            onClick={() => {
              setMode(t.key);
              setArmed(false);
            }}
            className={
              "rounded px-3 py-1 text-[11.5px] font-semibold " +
              (mode === t.key
                ? "bg-accent text-white"
                : "text-fg-muted hover:text-fg")
            }
          >
            {t.label}
          </button>
        ))}
      </div>

      <p className="text-[12px] text-fg-muted">
        {mode === "now" ? "Enviar ahora a " : "Programar el envío a "}
        <span className="font-semibold tabular-nums text-fg">
          {fmtN(recipients)} destinatarios
        </span>
        {" · costo ≈ "}
        <span className="font-semibold text-fg">{cost}</span> (≈{costCop} COP
        aprox)
      </p>

      {mode === "now" ? (
        !armed ? (
          <button
            type="button"
            disabled={disabled}
            onClick={() => setArmed(true)}
            className="self-start rounded-md bg-accent px-4 py-2 text-[12.5px] font-semibold text-white hover:opacity-90 disabled:opacity-50"
          >
            Enviar ahora
          </button>
        ) : (
          <div className="flex flex-wrap items-center gap-2 rounded-md border border-warn/40 bg-warn-soft px-3 py-2">
            <span className="text-[12px] font-medium text-fg">
              ¿Confirmar envío a {fmtN(recipients)} contactos por {cost}?
            </span>
            <button
              type="button"
              disabled={send.isPending}
              onClick={() =>
                send.mutate(null, { onSuccess: () => setArmed(false) })
              }
              className="rounded-md bg-accent px-3 py-1.5 text-[12px] font-semibold text-white hover:opacity-90 disabled:opacity-50"
            >
              Confirmar
            </button>
            <button
              type="button"
              onClick={() => setArmed(false)}
              className="rounded-md border border-line px-3 py-1.5 text-[12px] font-semibold text-fg hover:bg-white/[0.05]"
            >
              Cancelar
            </button>
          </div>
        )
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="date"
            disabled={!editable}
            value={date}
            onChange={(e) => setDate(e.target.value)}
            aria-label="Fecha de envío"
            className="rounded-md border border-line bg-transparent px-2.5 py-1.5 text-[12.5px] text-fg outline-none focus:border-accent disabled:opacity-60"
          />
          <input
            type="time"
            disabled={!editable}
            value={time}
            onChange={(e) => setTime(e.target.value)}
            aria-label="Hora de envío"
            className="rounded-md border border-line bg-transparent px-2.5 py-1.5 text-[12.5px] text-fg outline-none focus:border-accent disabled:opacity-60"
          />
          <button
            type="button"
            disabled={disabled || !date || !time}
            onClick={() => {
              const ms = new Date(`${date}T${time}`).getTime();
              if (!Number.isFinite(ms) || ms <= Date.now()) {
                setScheduleHint("La fecha debe estar en el futuro.");
                return;
              }
              setScheduleHint(null);
              send.mutate(ms);
            }}
            className="rounded-md bg-accent px-4 py-2 text-[12.5px] font-semibold text-white hover:opacity-90 disabled:opacity-50"
          >
            Programar envío
          </button>
        </div>
      )}

      {!canSend ? (
        <p className="text-[11px] text-fg-faint">
          Completá objetivo, mensaje y audiencia para habilitar el envío.
        </p>
      ) : null}
      {scheduleHint ? (
        <p className="text-[11.5px] text-danger">{scheduleHint}</p>
      ) : null}
      {send.error ? (
        <p className="text-[11.5px] leading-snug text-danger">
          {apiErrorDetail(send.error)}
        </p>
      ) : null}
      {send.data ? (
        <p className="text-[11.5px] text-ok">
          {send.data.scheduled
            ? "Envío programado — el workflow arrancará a la hora elegida."
            : "Envío arrancado."}{" "}
          <span className="text-fg-faint">({send.data.workflowId})</span>
        </p>
      ) : null}
    </div>
  );
}
